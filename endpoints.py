import os

# Google's OAuth 2.0 endpoints
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/auth"
CODE_ENDPOINT = "https://accounts.google.com/o/oauth2/token"
TOKENINFO_ENDPOINT = "https://accounts.google.com/o/oauth2/tokeninfo"
USERINFO_ENDPOINT = 'https://www.googleapis.com/oauth2/v1/userinfo'
SCOPE = "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile"
LOGOUT_URI = 'https://accounts.google.com/logout'
SESSION = '1'

# client ID / secret & cookie key
CLIENT_ID = '970367421437.apps.googleusercontent.com'
#devCLIENT_ID = '970367421437-n3q36vhobro6ai4hsnpe6khss36dh8ed.apps.googleusercontent.com'
CLIENT_SECRET = 's6F3DCcGC7j6lm76bdnxi6Ls'
#devCLIENT_SECRET = '7iqy4EyQS01xWraBeF06v6A8'
COOKIE_KEY = 'createacookiekey - probably using os.urandom(64)'

is_secure = os.environ.get('HTTPS') == 'on'
protocol = {False: 'http', True: 'https'}[is_secure]

ROOT_URI = protocol +'://' + os.environ["HTTP_HOST"]

RESPONSE_TYPE='token'

if (RESPONSE_TYPE == 'token'):
    REDIRECT_URI = ROOT_URI + '/oauthcallback'
elif (RESPONSE_TYPE == 'code'):
    REDIRECT_URI = ROOT_URI + '/code'
else:
    REDIRECT_URI = ROOT_URI + '/code'

CATCHTOKEN_URI = ROOT_URI + '/catchtoken'