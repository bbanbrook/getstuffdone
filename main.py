#!/usr/bin/env python
#
# Copyright 2012 Google Inc.
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
#

"""Starting template for Google App Engine applications.

Use this project as a starting point if you are just beginning to build a Google
App Engine project. Remember to download the OAuth 2.0 client secrets which can
be obtained from the Developer Console <https://code.google.com/apis/console/>
and save them as 'client_secrets.json' in the project directory.
"""

import httplib2
import logging
import os
import pickle
import codecs
import json
import tasks
import time
import datetime

from pytz.gae import pytz
from pprint import pprint
from django.utils.encoding import smart_str, smart_unicode
from apiclient.discovery import build
from oauth2client.appengine import oauth2decorator_from_clientsecrets
from oauth2client.client import AccessTokenRefreshError
from google.appengine.api import memcache
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext.webapp.util import login_required
from google.appengine.api import urlfetch
from gaesessions import get_current_session


import auth



# Set to true if we want to have our webapp print stack traces, etc
_DEBUG = False

# Add our custom Django template filters to the built in filters
template.register_template_library('tags.templatefilters')

# CLIENT_SECRETS, name of a file containing the OAuth 2.0 information for this
# application, including client_id and client_secret.
# You can see the Client ID and Client secret on the API Access tab on the
# Google APIs Console <https://code.google.com/apis/console>
CLIENT_SECRETS = os.path.join(os.path.dirname(__file__), 'client_secrets.json')

# Helpful message to display in the browser if the CLIENT_SECRETS file
# is missing.
MISSING_CLIENT_SECRETS_MESSAGE = """
<h1>Warning: Please configure OAuth 2.0</h1>
<p>
To make this sample run you will need to populate the client_secrets.json file
found at:
</p>
<code>%s</code>
<p>You can find the Client ID and Client secret values
on the API Access tab in the <a
href="https://code.google.com/apis/console">APIs Console</a>.
</p>

""" % CLIENT_SECRETS


http = httplib2.Http(memcache)
service = build("calendar", "v3", http=http)

# Set up an OAuth2Decorator object to be used for authentication.  Add one or
# more of the following scopes in the scopes parameter below. PLEASE ONLY ADD
# THE SCOPES YOU NEED. For more information on using scopes please see
# <https://developers.google.com/+/best-practices>.
decorator = oauth2decorator_from_clientsecrets(
    CLIENT_SECRETS,
    scope=[
      'https://www.googleapis.com/auth/calendar.readonly',
      'https://www.googleapis.com/auth/calendar',
    ],
    message=MISSING_CLIENT_SECRETS_MESSAGE)

class MainHandler(webapp.RequestHandler):

  @decorator.oauth_required
  def get(self):
    # Get the authorized Http object created by the decorator.
    http = decorator.http()

    """events_feed = service.events().list(alt='json', calendarId='primary', singleEvents='true', fields='items(summary,start,end)', timeMin='2013-04-14T17:58:12.000Z', timeMax='2013-04-17T17:58:12.000Z')
    events = events_feed.execute(http=http)
    if events['items']:
        for event in events['items']:
            s = event['start']
            print '%s <br>' % (smart_str(event['summary']))
            print s['dateTime']
            #print '%s --' % (smart_str(event['start']))
            
            print 
            #j = json.load(event['start'])
    #self.response.write(json.dumps(events))
    #j = json.loads(event['start'])
    #print j['items']"""
    

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

    #self.redirect(self.request.get('next'))

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


class InboxPage(BaseRequestHandler):
  """Lists the task list "inbox" for the current user."""
  @login_required
  @decorator.oauth_required
  
  def get(self):
    session = get_current_session()
    #print 'test  %s' % (session['access_token'])
        
    lists = TaskList.get_current_user_lists()
    show_archive = self.request.get('archive')
    if not show_archive:
      non_archived = []
      for task_list in lists:
        if not task_list.archived:
          non_archived.append(task_list)
      lists = non_archived
    
    http = decorator.http()
    
    # get start and end time
    local_tz = pytz.timezone('America/Los_Angeles')
    utc_dt = pytz.utc.localize(datetime.datetime.utcnow())
    
    local_dt = utc_dt.astimezone(local_tz)
    
    # create a new datetime for the start of the day and add a day to it to get tomorrow.
    start = datetime.datetime(local_dt.year, local_dt.month, local_dt.day).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    end = datetime.datetime(local_dt.year, local_dt.month, local_dt.day).strftime("%Y-%m-%dT23:59:59.%fZ")

    events_feed = service.events().list(alt='json', calendarId='primary', singleEvents='true', fields='items(summary,start,end)', timeMin=start, timeMax=end)
    events = events_feed.execute(http=http)
    
    cal_list = service.calendarList().list()
    calendars = cal_list.execute(http=http)
    
    for calendar in calendars['items']:
        #print calendar['id']
        events_feed = service.events().list(alt='json', calendarId=calendar['id'], singleEvents='true', fields='items(summary,start,end)', timeMin=start, timeMax=end)
        events.update(events_feed.execute(http=http))
        
    tot_time = 0
    
    for event in events['items']:
        s = event['start']
        if s['dateTime']:
            s['dateTime'] = datetime.datetime.strptime(s['dateTime'][:19],'%Y-%m-%dT%H:%M:%S')
            e = event['end']
            e['dateTime'] = datetime.datetime.strptime(e['dateTime'][:19],'%Y-%m-%dT%H:%M:%S')
        
        # convert to unix timestamp
        start_ts = time.mktime(s['dateTime'].timetuple())
        end_ts = time.mktime(e['dateTime'].timetuple())
        
        # they are now in seconds, subtract and then divide by 60 to get minutes.
        tot_time = tot_time + int(end_ts-start_ts) / 3600
    
    
    self.generate('index.html', {
      'lists': lists,
      'archive': show_archive,
      'events': events,
      'tot_time': tot_time
    })
    
    """if events['items']:
        for event in events['items']:
            s = event['start']
            print '%s <br>' % (smart_str(event['summary']))
            print s['dateTime']
            #print '%s --' % (smart_str(event['start']))
            
            print """

app = webapp.WSGIApplication(
  [
    ('/main', MainHandler),
    ('/', InboxPage),
    ('/list', TaskListPage),
    ('/edittask.do', tasks.EditTaskAction),
    ('/createtasklist.do', tasks.CreateTaskListAction),
    ('/addmember.do', tasks.AddMemberAction),
    ('/inboxaction.do', InboxAction),
    ('/tasklist.do', TaskListAction),
    ('/publishtasklist.do', tasks.PublishTaskListAction),
    ('/settaskcompleted.do', SetTaskCompletedAction),
    ('/settaskpositions.do', tasks.SetTaskPositionsAction),
    ('/events.do', tasks.EventsPage),
    ('/create.do', tasks.CreateEvent),
   (decorator.callback_path, decorator.callback_handler()),
  ],
  debug=True)
    
def main():
    wsgiref.handlers.CGIHandler().run(app)

if __name__ == '__main__':
  main()


if __name__ == '__main__':
  main()
