#!/usr/bin/env python
#
# Copyright 2008 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A collaborative task list web application built on Google App Engine."""

__author__ = 'Bret Taylor'

import datetime
import os
import random
import string
import sys
import wsgiref.handlers

from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import login_required
from google.appengine.api import urlfetch
from django.utils import simplejson as json
from gaesessions import get_current_session

import atom
import gdata.service
import gdata.auth
import gdata.alt.appengine
import gdata.calendar
import gdata.calendar.service
import endpoints
import logging
import urllib
import auth

# Set to true if we want to have our webapp print stack traces, etc
_DEBUG = False

# Add our custom Django template filters to the built in filters
template.register_template_library('tags.templatefilters')

class TaskList(db.Model):
  """A TaskList is the entity tasks refer to to form a list.

  Other than the tasks referring to it, a TaskList just has meta-data, like
  whether it is published and the date at which it was last updated.
  """
  name = db.StringProperty(required=True)
  created = db.DateTimeProperty(auto_now_add=True)
  updated = db.DateTimeProperty(auto_now=True)
  archived = db.BooleanProperty(default=False)
  published = db.BooleanProperty(default=False)

  @staticmethod
  def get_current_user_lists():
    """Returns the task lists that the current user has access to."""
    return TaskList.get_user_lists(users.GetCurrentUser())

  @staticmethod
  def get_user_lists(user):
    """Returns the task lists that the given user has access to."""
    if not user: return []
    memberships = db.Query(TaskListMember).filter('user =', user)
    return [m.task_list for m in memberships]

  def current_user_has_access(self):
    """Returns true if the current user has access to this task list."""
    return self.user_has_access(users.GetCurrentUser())

  def user_has_access(self, user):
    """Returns true if the given user has access to this task list."""
    if not user: return False
    query = db.Query(TaskListMember)
    query.filter('task_list =', self)
    query.filter('user =', user)
    return query.get()


class TaskListMember(db.Model):
  """Represents the many-to-many relationship between TaskLists and Users.

  This is essentially the task list ACL.
  """
  task_list = db.Reference(TaskList, required=True)
  user = db.UserProperty(required=True)


class Task(db.Model):
  """Represents a single task in a task list.

  A task basically only has a description. We use the priority field to
  order the tasks so that users can specify task order manually.

  The completed field is a DateTime, not a bool; if it is not None, the
  task is completed, and the timestamp represents the time at which it was
  marked completed.
  """
  description = db.TextProperty(required=True)
  completed = db.DateTimeProperty()
  archived = db.BooleanProperty(default=False)
  priority = db.IntegerProperty(required=True, default=0)
  task_list = db.Reference(TaskList)
  created = db.DateTimeProperty(auto_now_add=True)
  updated = db.DateTimeProperty(auto_now=True)


class BaseRequestHandler(webapp.RequestHandler):
  """Supplies a common template generation function.

  When you call generate(), we augment the template variables supplied with
  the current user in the 'user' variable and the current webapp request
  in the 'request' variable.
  """
  def generate(self, template_name, template_values={}):
    values = {
      'request': self.request,
      'user': users.GetCurrentUser(),
      'login_url': users.CreateLoginURL(self.request.uri),
      'logout_url': users.CreateLogoutURL('http://' + self.request.host + '/'),
      'debug': self.request.get('deb'),
      'application_name': 'Task Manager',
    }
    values.update(template_values)
    directory = os.path.dirname(__file__)
    path = os.path.join(directory, os.path.join('templates', template_name))
    self.response.out.write(template.render(path, values, debug=_DEBUG))


class InboxPage(BaseRequestHandler):
  """Lists the task list "inbox" for the current user."""
  @login_required
  
  def get(self):
    session = get_current_session()
    print 'test  %s' % (session['access_token'])
        
    lists = TaskList.get_current_user_lists()
    show_archive = self.request.get('archive')
    if not show_archive:
      non_archived = []
      for task_list in lists:
        if not task_list.archived:
          non_archived.append(task_list)
      lists = non_archived
    self.generate('index.html', {
      'lists': lists,
      'archive': show_archive,
    })
    
def DateRangeQuery(calendar_service, start_date='2007-01-01', end_date='2013-07-01'):
  print 'Date range query for events on Primary Calendar: %s to %s' % (start_date, end_date,)
  query = gdata.calendar.service.CalendarEventQuery('default', 'private', 'full')
  query.start_min = start_date
  query.start_max = end_date
  feed = calendar_service.CalendarQuery(query)
  for i, an_event in enumerate(feed.entry):
    print '\t%s. %s' % (i, an_event.title.text,)
    for a_when in an_event.when:
      print '\t\tStart time: %s' % (a_when.start_time,)
      print '\t\tEnd time:   %s' % (a_when.end_time,)
      

class TaskListPage(BaseRequestHandler):
  """Displays a single task list based on ID.

  If the task list is not published, we give a 403 unless the user is a
  collaborator on the list. If it is published, but the user is not a
  collaborator, we show the more limited HTML view of the task list rather
  than the interactive AJAXy edit page.
  """

  # The different task list output types we support: content types and
  # template file extensions
  _OUTPUT_TYPES = {
    'default': ['text/html', 'html'],
    'html': ['text/html', 'html'],
    'atom': ['application/atom+xml', 'xml'],
  }

  def get(self):
    task_list = TaskList.get(self.request.get('id'))
    if not task_list:
      self.error(403)
      return

    # Choose a template based on the output type
    output_name = self.request.get('output')
    output_name_list = TaskListPage._OUTPUT_TYPES.keys()
    if output_name not in output_name_list:
      output_name = output_name_list[0]
    output_type = TaskListPage._OUTPUT_TYPES[output_name]

    # Validate this user has access to this task list. If not, they can
    # access the html view of this list only if it is published.
    if not task_list.current_user_has_access():
      if task_list.published:
        if output_name == 'default':
          output_name = 'html'
          output_type = TaskListPage._OUTPUT_TYPES[output_name]
      else:
        user = users.GetCurrentUser()
        if not user:
          self.redirect(users.CreateLoginURL(self.request.uri))
        else:
          self.error(403)
        return

    # Filter out archived tasks by default
    show_archive = self.request.get('archive')
    tasks = task_list.task_set.order('-priority').order('created')
    if not show_archive:
      tasks.filter('archived =', False)
    tasks = list(tasks)

    # Get the last updated date from the list of tasks
    if len(tasks) > 0:
      updated = max([task.updated for task in tasks])
    else:
      updated = None

    self.response.headers['Content-Type'] = output_type[0]
    self.generate('tasklist_' + output_name + '.' + output_type[1], {
      'task_list': task_list,
      'tasks': tasks,
      'archive': show_archive,
      'updated': updated,
    })


class CreateTaskListAction(BaseRequestHandler):
  """Creates a new task list for the current user."""
  def post(self):
    user = users.GetCurrentUser()
    name = self.request.get('name')
    if not user or not name:
      self.error(403)
      return

    task_list = TaskList(name=name)
    task_list.put()
    task_list_member = TaskListMember(task_list=task_list, user=user)
    task_list_member.put()

    if self.request.get('next'):
      self.redirect(self.request.get('next'))
    else:
      self.redirect('/') #list?id=' + str(task_list.key()))


class EditTaskAction(BaseRequestHandler):
  """Edits a specific task, changing its description.

  We also updated the last modified date of the task list so that the
  task list inbox shows the correct last modified date for the list.

  This can be used in an AJAX way or in a form. In a form, you should
  supply a "next" argument that denotes the URL we should redirect to
  after the edit is complete.
  """
  def post(self):
    description = self.request.get('description')
    if not description:
      self.error(403)
      return

    # Get the existing task that we are editing
    task_key = self.request.get('task')
    if task_key:
      task = Task.get(task_key)
      if not task:
        self.error(403)
        return
      task_list = task.task_list
    else:
      task = None
      task_list = TaskList.get(self.request.get('list'))

    # Validate this user has access to this task list
    if not task_list or not task_list.current_user_has_access():
      self.error(403)
      return

    # Create the task
    if task:
      task.description = db.Text(description)
    else:
      task = Task(description=db.Text(description), task_list=task_list)
    task.put()

    # Update the task list so it's updated date is updated. Saving it is all
    # we need to do since that field has auto_now=True
    task_list.put()

    # Only redirect if "next" is given
    next = self.request.get('next')
    if next:
      self.redirect(next)
    else:
      self.response.headers['Content-Type'] = 'text/plain'
      self.response.out.write(str(task.key()))


class AddMemberAction(BaseRequestHandler):
  """Adds a new User to a TaskList ACL."""
  def post(self):
    task_list = TaskList.get(self.request.get('list'))
    email = self.request.get('email')
    if not task_list or not email:
      self.error(403)
      return

    # Validate this user has access to this task list
    if not task_list.current_user_has_access():
      self.error(403)
      return

    # Don't duplicate entries in the permissions datastore
    user = users.User(email)
    if not task_list.user_has_access(user):
      member = TaskListMember(user=user, task_list=task_list)
      member.put()
    self.redirect(self.request.get('next'))


class InboxAction(BaseRequestHandler):
  """Performs an action in the user's TaskList inbox.

  We support Archive, Unarchive, and Delete actions. The action is specified
  by the "action" argument in the POST. The names are capitalized because
  they correspond to the text in the buttons in the form, which all have the
  name "action".
  """
  def post(self):
    action = self.request.get('action')
    lists = self.request.get('list', allow_multiple=True)
    if not action in ['Archive', 'Unarchive', 'Delete']:
      self.error(403)
      return

    for key in lists:
      task_list = TaskList.get(key)

      # Validate this user has access to this task list
      if not task_list or not task_list.current_user_has_access():
        self.error(403)
        return

      if action == 'Archive':
        task_list.archived = True
        task_list.put()
      elif action == 'Unarchive':
        task_list.archived = False
        task_list.put()
      else:
        for member in task_list.tasklistmember_set:
          member.delete()
        for task in task_list.task_set:
          task.delete()
        task_list.delete()

    self.redirect(self.request.get('next'))


class TaskListAction(BaseRequestHandler):
  """Performs an action on a specific task list.

  The actions we support are "Archive Completed" and "Delete", as specified
  by the "action" argument in the POST.
  """
  def post(self):
    action = self.request.get('action')
    tasks = self.request.get('task', allow_multiple=True)
    if not action in ['Archive Completed', 'Delete']:
      self.error(403)
      return

    for key in tasks:
      task = Task.get(key)

      # Validate this user has access to this task list
      if not task or not task.task_list.current_user_has_access():
        self.error(403)
        return

      if action == 'Delete':
        task.delete()
      else:
        if task.completed and not task.archived:
          task.priority = 0
          task.archived = True
          task.put()

    self.redirect(self.request.get('next'))


class SetTaskCompletedAction(BaseRequestHandler):
  """Sets a given task to be completed at the current time."""
  def post(self):
    task = Task.get(self.request.get('id'))
    if not task or not task.task_list.current_user_has_access():
      self.error(403)
      return

    completed = self.request.get('completed')
    if completed:
      task.completed = datetime.datetime.now()
    else:
      task.completed = None
      task.archived = False
    task.put()


class SetTaskPositionsAction(BaseRequestHandler):
  """Orders the tasks in a task lists.

  The input to this handler is a comma-separated list of task keys in the
  "tasks" argument to the post. We assign priorities to the given tasks
  based on that order (e.g., 1 through N for N tasks).
  """
  def post(self):
    keys = self.request.get("tasks").split(",")
    if not keys:
      self.error(403)
      return
    num_keys = len(keys)
    for i, key in enumerate(keys):
      key = keys[i]
      task = Task.get(key)
      if not task or not task.task_list.current_user_has_access():
        self.error(403)
        return
      task.priority = num_keys - i - 1
      task.put()


class PublishTaskListAction(BaseRequestHandler):
  """Publishes a given task list, which makes it viewable by everybody."""
  def post(self):
    task_list = TaskList.get(self.request.get('id'))
    if not task_list or not task_list.current_user_has_access():
      self.error(403)
      return

    task_list.published = bool(self.request.get('publish'))
    task_list.put()

class Event(db.Model):
  title = db.StringProperty(required=True)
  description = db.TextProperty()
  time = db.DateTimeProperty()
  location = db.TextProperty()
  creator = db.UserProperty()
  edit_link = db.TextProperty()
  gcal_event_link = db.TextProperty()
  gcal_event_xml = db.TextProperty()
  
class Attendee(db.Model):
  email = db.StringProperty()
  event = db.ReferenceProperty(Event)

class EventsPage(BaseRequestHandler):
  def get(self):
      
      template_info = {'target_url' : get_target_url()}
      
      if ('access_token' in session):
          # we need to validate the access_token (long-lived sessions, token might have timed out)
          if(validate_access_token(session['access_token'])):            
              # get the user profile information (USERINFO)
              userinfo = json.loads(urlfetch.fetch(endpoints.USERINFO_ENDPOINT,
                                                  headers={'Authorization': 'Bearer ' + session['access_token']}).content)
              
              template_info = {
                                'target_url' : get_target_url(),
                                'userinfo' : userinfo
                              }
      
      self.response.out.write(template.render('templates/profileview.html', template_info))

class CreateEvent(BaseRequestHandler):
  title = 'Create!'
  
  def get(self):
    """Show the event creation form"""
    #self.write_page_header()
    template_values = {}
    path = os.path.join(os.path.dirname(__file__), 'templates')
    path = os.path.join(path, 'create.html')
    self.response.out.write(template.render(path, template_values))
    #self.write_page_footer()
  
  def post(self):
    """Create an event and store it in the datastore.
    
    This event does not exist in Google Calendar. The event creator can add it
    to Google Calendar on the 'events' page.
    """
    #self.write_page_header()
    
    # Create an event in the datastore.
    new_event = Event(title=self.request.get('name'), 
                      creator=users.get_current_user(),
                      # Take the time string passing in by JavaScript in the
                      # form and convert to a datetime object.
                      time=datetime.datetime.strptime(
                          self.request.get('datetimestamp'), '%d/%m/%Y %H:%M'),
                      description=self.request.get('description'),
                      location=self.request.get('location'))
    new_event.put()
    
    # Associate each of the attendees with the event in the datastore.
    attendee_list = []
    if self.request.get('attendees'):
      attendee_list = self.request.get('attendees').split(',')
      if attendee_list:
        # TODO: perform one put with a list of Attendee objects.
        for attendee in attendee_list:
          new_attendee = Attendee(email=attendee.strip(), event=new_event)
          new_attendee.put()
          
    template_values = {
        'name': new_event.title,
        'description': new_event.description,
        'time': new_event.time.strftime('%x %X %Z'),
        'location': new_event.location
        }
    path = os.path.join(os.path.dirname(__file__), 'templates')
    path = os.path.join(path, 'created.html')
    self.response.out.write(template.render(path, template_values))
    #self.write_page_footer()
  
  def post(self):
    """Adds an event to Google Calendar."""
    event_id = self.request.get('event_id')
    
    # Fetch the event from the datastore and make sure that the current user
    # is an owner since only event owners are allowed to create a calendar 
    # event.
    event = Event.get_by_id(long(event_id))
    
    if users.get_current_user() == event.creator:
      # Create a new Google Calendar event.
      event_entry = gdata.calendar.CalendarEventEntry()
      event_entry.title = atom.Title(text=event.title)
      event_entry.content = atom.Content(text=event.description)
      start_time = '%s.000Z' % event.time.isoformat()
      event_entry.when.append(gdata.calendar.When(start_time=start_time))
      event_entry.where.append(
          gdata.calendar.Where(value_string=event.location))
      # Add a who element for each attendee.
      attendee_list = event.attendee_set
      if attendee_list:
        for attendee in attendee_list:
          new_attendee = gdata.calendar.Who()
          new_attendee.email = attendee.email
          event_entry.who.append(new_attendee)
      
      # Send the event information to Google Calendar and receive a 
      # Google Calendar event.
      try:
        cal_event = self.calendar_client.InsertEvent(event_entry, 
            'http://www.google.com/calendar/feeds/default/private/full')
        edit_link = cal_event.GetEditLink()
        if edit_link and edit_link.href:
          # Add the edit link to the Calendar event to use for making changes.
          event.edit_link = edit_link.href
        alternate_link = cal_event.GetHtmlLink()
        if alternate_link and alternate_link.href:
          # Add a link to the event in the Google Calendar HTML web UI.
          event.gcal_event_link = alternate_link.href
          event.gcal_event_xml = str(cal_event)
        event.put()
      # If adding the event to Google Calendar failed due to a bad auth token,
      # remove the user's auth tokens from the datastore so that they can 
      # request a new one. 
      except gdata.service.RequestError, request_exception:
        request_error = request_exception[0]
        if request_error['status'] == 401 or request_error['status'] == 403:
          gdata.alt.appengine.save_auth_tokens({})
        # If the request failure was not due to a bad auth token, reraise the
        # exception for handling elsewhere.
        else:
          raise
    else:
      self.response.out.write('I\'m sorry, you don\'t have permission to add'
                              ' this event to Google Calendar.')
    
    # Display the list of events also as if this were a get.
    self.get()

app = webapp.WSGIApplication([
    ('/', InboxPage),
    ('/list', TaskListPage),
    ('/edittask.do', EditTaskAction),
    ('/createtasklist.do', CreateTaskListAction),
    ('/addmember.do', AddMemberAction),
    ('/inboxaction.do', InboxAction),
    ('/tasklist.do', TaskListAction),
    ('/publishtasklist.do', PublishTaskListAction),
    ('/settaskcompleted.do', SetTaskCompletedAction),
    ('/settaskpositions.do', SetTaskPositionsAction),
    ('/events.do', EventsPage),
    ('/create.do', CreateEvent),
    ('/oauthcallback', auth.CallbackHandler),
    ('/catchtoken', auth.CatchTokenHandler),
    ('/profile', auth.ProfileHandler),
    ('/logout', auth.LogoutHandler),
    ('/code', auth.CodeHandler),
  ], debug=_DEBUG)

def main():
    wsgiref.handlers.CGIHandler().run(app)


if __name__ == '__main__':
  main()
