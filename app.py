import os 
import json
import logging
from flask import Flask, render_template, request, redirect, session
from identity.flask import Auth 
import requests 
import app_config

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(app_config)

# Session configuration
app.config['SECRET_KEY'] = 'pov8Q~WX4Y3TnFDtcWKhvdpTYZsdeSWcBFdZiawZ'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Define scopes - IMPORTANT: Use full URIs for Graph API
SCOPES = [
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.Read"
]

auth = Auth(
   app,
   authority=app.config['AUTHORITY'],
   client_id=app.config['CLIENT_ID'],
   client_credential=app.config['CLIENT_SECRET'],
   redirect_uri=app.config['REDIRECT_URI']  
)

@app.route("/")
@auth.login_required
def index(*, context):
   return render_template( 
      "index.html", 
      user=context['user'],
      title="This is a sample flask app", 
      api_endpoint=os.getenv("ENDPOINT")
   )

@app.route("/logout")
def logout():
   session.clear()
   return redirect(f"https://login.microsoftonline.com/eb8b08ab-d864-425c-992c-4d988e89d29f/oauth2/v2.0/logout?post_logout_redirect_uri=http://localhost:8000")

@app.route("/clear_cache")
def clear_cache():
   """Clear token cache and session to force fresh authentication"""
   import shutil
   session.clear()
   
   # Clear Flask-Session cache
   cache_dir = os.path.join(os.getcwd(), 'flask_session')
   if os.path.exists(cache_dir):
       try:
           shutil.rmtree(cache_dir)
           logger.info("Cleared flask_session directory")
       except Exception as e:
           logger.error(f"Error clearing cache: {e}")
   
   return """
   <h2>✓ Cache Cleared</h2>
   <p>Token cache and session have been cleared.</p>
   <p><a href="/logout">Now logout and login again</a></p>
   """

@app.route("/debug_token")
@auth.login_required(scopes=SCOPES)
def debug_token(*, context):
    import base64
    
    token = context.get('access_token')
    
    if not token:
        return "<h3>No access token found!</h3><p>Scopes requested: " + str(SCOPES) + "</p>"
    
    try:
        parts = token.split('.')
        if len(parts) == 3:
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding
            
            decoded_bytes = base64.urlsafe_b64decode(payload)
            decoded_json = json.loads(decoded_bytes.decode('utf-8'))
            
            # Log token details
            logger.info(f"Token scopes: {decoded_json.get('scp', 'NO SCP')}")
            logger.info(f"Token audience: {decoded_json.get('aud')}")
            logger.info(f"Token issued at: {decoded_json.get('iat')}")
            logger.info(f"Token expires at: {decoded_json.get('exp')}")
            
            return f"""
            <h2>Token Debug Info</h2>
            <h3>Scopes in Token:</h3>
            <pre>{decoded_json.get('scp', 'NO SCP') or decoded_json.get('roles', 'NO ROLES')}</pre>
            
            <h3>Audience:</h3>
            <pre>{decoded_json.get('aud')}</pre>
            
            <h3>Issued For:</h3>
            <pre>{decoded_json.get('upn', decoded_json.get('email', 'N/A'))}</pre>
            
            <h3>App ID:</h3>
            <pre>{decoded_json.get('appid')}</pre>
            
            <h3>Token Valid:</h3>
            <pre>Issued: {decoded_json.get('iat')} | Expires: {decoded_json.get('exp')}</pre>
            
            <h3>Full Token Payload:</h3>
            <pre>{json.dumps(decoded_json, indent=2)}</pre>
            
            <p><a href="/logout">Logout</a> | <a href="/">Home</a></p>
            """
    except Exception as e:
        logger.error(f"Token decode error: {str(e)}", exc_info=True)
        return f"<h3>Error: {str(e)}</h3>"

@app.route("/call_api") 
@auth.login_required(scopes=SCOPES)   
def call_downstream_api(*, context):
    if not context.get("access_token"):
        return render_template("display.html", 
                             title="Api Response", 
                             result={"error": "No access token. Check SCOPE configuration."})
    
    try:
        logger.info(f"Calling API: {os.getenv('ENDPOINT')}")
        logger.debug(f"Token length: {len(context['access_token'])}")
        
        response = requests.get(
            os.getenv("ENDPOINT"),
            headers={"Authorization": "Bearer " + context["access_token"]},
            timeout=30
        )
        
        logger.info(f"API Response Status: {response.status_code}")
        
        if response.status_code == 200:
            if response.text:
                api_result = response.json()
            else:
                api_result = {"error": "Empty response from API"}
        else:
            api_result = {
                "error": f"HTTP {response.status_code}",
                "message": response.text[:500] if response.text else "No response body"
            }
            logger.error(f"API Error: {response.status_code} - {response.text[:200]}")
            
    except requests.exceptions.JSONDecodeError:
        api_result = {
            "error": "JSON Decode Error",
            "message": "API returned non-JSON response",
            "response_text": response.text[:500] if 'response' in locals() and response.text else "Empty"
        }
        logger.error("JSON decode error", exc_info=True)
    except requests.exceptions.RequestException as e:
        api_result = {
            "error": "Request Failed",
            "message": str(e)
        }
        logger.error(f"Request exception: {str(e)}", exc_info=True)
   
    return render_template("display.html", title="Api Response", result=api_result)

@app.route("/mails", methods=['GET', 'POST'])
@auth.login_required(scopes=SCOPES)
def view_emails(*, context):
    api_response = None
    
    if request.method == 'POST':
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")
        
        token = context.get('access_token')
        
        logger.info("=" * 80)
        logger.info("EMAIL FETCH REQUEST STARTED")
        logger.info(f"Date range: {start_date} to {end_date}")
        logger.info(f"Token present: {bool(token)}")
        
        if not token:
            api_response = {
                'error': 'No Access Token',
                'message': 'Please logout and login again',
                'logout_link': '/logout'
            }
            logger.error("No access token found in context")
            
        elif not start_date or not end_date:
            api_response = {
                'error': 'Missing Parameters',
                'message': 'Both start_date and end_date are required'
            }
            logger.warning("Missing date parameters")
            
        else:
            try:
                # Build the query
                query_params = (
                    f"$filter=receivedDateTime ge {start_date}T00:00:00Z"
                    f" and receivedDateTime le {end_date}T23:59:59Z"
                    f"&$select=subject,from,receivedDateTime"
                    f"&$top=50"
                    f"&$orderby=receivedDateTime desc"
                )
                
                full_url = f"{os.getenv('ENDPOINT')}?{query_params}"
                
                logger.info(f"Full URL: {full_url}")
                logger.info(f"Token (first 30 chars): {token[:30]}...")
                logger.info(f"Token (last 30 chars): ...{token[-30:]}")
                
                # Decode and log token info
                try:
                    import base64
                    parts = token.split('.')
                    if len(parts) == 3:
                        payload = parts[1]
                        padding = 4 - len(payload) % 4
                        if padding != 4:
                            payload += '=' * padding
                        decoded_bytes = base64.urlsafe_b64decode(payload)
                        token_data = json.loads(decoded_bytes.decode('utf-8'))
                        logger.info(f"Token scopes: {token_data.get('scp')}")
                        logger.info(f"Token audience: {token_data.get('aud')}")
                        logger.info(f"Token app ID: {token_data.get('appid')}")
                except Exception as e:
                    logger.warning(f"Could not decode token: {e}")
                
                response = requests.get(
                    url=full_url,
                    headers={
                        'Authorization': f'Bearer {token}',
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    },
                    timeout=30
                )
                
                logger.info(f"Response Status Code: {response.status_code}")
                logger.info(f"Response Headers: {dict(response.headers)}")
                
                if response.status_code == 200:
                    if response.text:
                        api_response = response.json()
                        email_count = len(api_response.get('value', []))
                        logger.info(f"✓ SUCCESS: Retrieved {email_count} emails")
                    else:
                        api_response = {'error': 'Empty response from API'}
                        logger.warning("Empty response body")
                        
                elif response.status_code == 401:
                    logger.error("=" * 80)
                    logger.error("401 UNAUTHORIZED ERROR")
                    logger.error(f"Response body: {response.text}")
                    logger.error("=" * 80)
                    
                    try:
                        error_detail = response.json()
                        logger.error(f"Error JSON: {json.dumps(error_detail, indent=2)}")
                    except:
                        error_detail = response.text
                        logger.error(f"Error text: {error_detail}")
                    
                    # Extract specific error info
                    error_code = None
                    error_message = None
                    
                    if isinstance(error_detail, dict):
                        if 'error' in error_detail:
                            error_info = error_detail['error']
                            if isinstance(error_info, dict):
                                error_code = error_info.get('code')
                                error_message = error_info.get('message')
                            else:
                                error_code = error_info
                        error_message = error_message or error_detail.get('error_description')
                    
                    api_response = {
                        'error': '401 Unauthorized',
                        'error_code': error_code,
                        'message': error_message or 'Authentication failed',
                        'details': error_detail,
                        'token_preview': f"{token[:30]}...{token[-30:]}",
                        'endpoint': full_url,
                        'help': 'Visit /debug_token to verify your scopes',
                        'troubleshooting': [
                            '1. Check if Mail.Read permission is granted in Azure Portal',
                            '2. Ensure admin consent is given',
                            '3. Try logout and login again to refresh token',
                            '4. Verify the token audience is correct (should be 00000003-0000-0000-c000-000000000000)'
                        ]
                    }
                    
                elif response.status_code == 403:
                    logger.error(f"403 FORBIDDEN: {response.text}")
                    api_response = {
                        'error': 'HTTP 403 Forbidden',
                        'message': 'Token is valid but lacks necessary permissions',
                        'details': response.text[:1000],
                        'help': 'Check Azure Portal API permissions and ensure admin consent is granted'
                    }
                    
                else:
                    logger.error(f"HTTP {response.status_code}: {response.text[:500]}")
                    api_response = {
                        'error': f'HTTP {response.status_code}',
                        'message': response.text[:1000] if response.text else 'No response body',
                        'headers': dict(response.headers)
                    }
                    
            except requests.exceptions.JSONDecodeError as e:
                logger.error("JSON Decode Error", exc_info=True)
                api_response = {
                    'error': 'JSON Decode Error',
                    'message': str(e),
                    'response_preview': response.text[:500] if 'response' in locals() and response.text else 'No response'
                }
            except requests.exceptions.RequestException as e:
                logger.error("Request Exception", exc_info=True)
                api_response = {
                    'error': 'Request Failed',
                    'message': str(e),
                    'exception_type': type(e).__name__
                }
        
        logger.info("EMAIL FETCH REQUEST COMPLETED")
        logger.info("=" * 80)
        
        return render_template(
            "mails.html", 
            user=context['user'], 
            title="View your emails here",
            result=api_response
        )
    
    # GET request - show the form
    return render_template(
        'mails.html',
        user=context.get('user'),
        title='View emails'
    )

@app.route("/test_token")
@auth.login_required(scopes=SCOPES)
def test_token(*, context):
    """Test if token can access Microsoft Graph"""
    token = context.get('access_token')
    
    if not token:
        return {"error": "No token"}, 400
    
    # Decode and show token details
    import base64
    try:
        parts = token.split('.')
        if len(parts) == 3:
            payload = parts[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += '=' * padding
            decoded_bytes = base64.urlsafe_b64decode(payload)
            token_info = json.loads(decoded_bytes.decode('utf-8'))
            
            logger.info(f"Token ver: {token_info.get('ver')}")
            logger.info(f"Token aud: {token_info.get('aud')}")
            logger.info(f"Token scp: {token_info.get('scp')}")
            logger.info(f"Token roles: {token_info.get('roles')}")
    except Exception as e:
        logger.error(f"Token decode error: {e}")
        token_info = {}
    
    # Try a simple Graph API call
    test_url = "https://graph.microsoft.com/v1.0/me"
    
    logger.info(f"Testing token with: {test_url}")
    
    response = requests.get(
        test_url,
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        },
        timeout=10
    )
    
    logger.info(f"Test response: {response.status_code}")
    logger.info(f"Test response body: {response.text[:200]}")
    
    if response.status_code == 200:
        user_data = response.json()
        return f"""
        <h2>✓ Token Works for /me endpoint!</h2>
        <p>Successfully authenticated as: {user_data.get('displayName')} ({user_data.get('mail')})</p>
        
        <h3>Token Info:</h3>
        <pre>Version: {token_info.get('ver')}
Audience: {token_info.get('aud')}
Scopes: {token_info.get('scp')}
App ID: {token_info.get('appid')}</pre>
        
        <h3>User Profile:</h3>
        <pre>{json.dumps(user_data, indent=2)}</pre>
        
        <hr>
        <h3>Now test Mail.Read:</h3>
        <p><a href="/test_mail_permission">Test /me/messages endpoint</a></p>
        <p><a href="/mails">Try fetching emails</a> | <a href="/">Home</a></p>
        """
    else:
        error_body = response.text
        try:
            error_json = response.json()
            error_detail = json.dumps(error_json, indent=2)
        except:
            error_detail = error_body
            
        return f"""
        <h2>✗ Token Test Failed</h2>
        <p>Status: {response.status_code}</p>
        <h3>Token Info:</h3>
        <pre>Version: {token_info.get('ver')}
Audience: {token_info.get('aud')}
Scopes: {token_info.get('scp')}
Roles: {token_info.get('roles')}</pre>
        <h3>Error:</h3>
        <pre>{error_detail}</pre>
        <p><a href="/clear_cache">Clear cache and try again</a> | <a href="/debug_token">Debug Token</a> | <a href="/">Home</a></p>
        """

@app.route("/test_mail_permission")
@auth.login_required(scopes=SCOPES)
def test_mail_permission(*, context):
    """Test specifically the Mail.Read permission"""
    token = context.get('access_token')
    
    if not token:
        return {"error": "No token"}, 400
    
    # Try accessing messages endpoint with minimal query
    test_url = "https://graph.microsoft.com/v1.0/me/messages?$top=1"
    
    logger.info(f"Testing Mail.Read with: {test_url}")
    
    response = requests.get(
        test_url,
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        },
        timeout=10
    )
    
    logger.info(f"Mail test response: {response.status_code}")
    logger.info(f"Mail test headers: {dict(response.headers)}")
    logger.info(f"Mail test body: {response.text[:500]}")
    
    if response.status_code == 200:
        data = response.json()
        count = len(data.get('value', []))
        return f"""
        <h2>✓ Mail.Read Permission Works!</h2>
        <p>Successfully retrieved {count} message(s)</p>
        <pre>{json.dumps(data, indent=2)}</pre>
        <p><a href="/mails">Go to full email viewer</a> | <a href="/">Home</a></p>
        """
    else:
        error_body = response.text
        try:
            error_json = response.json()
            error_detail = json.dumps(error_json, indent=2)
        except:
            error_detail = error_body if error_body else "Empty response"
            
        return f"""
        <h2>✗ Mail.Read Test Failed</h2>
        <p>Status: {response.status_code}</p>
        <p>Headers: <pre>{json.dumps(dict(response.headers), indent=2)}</pre></p>
        <h3>Error Detail:</h3>
        <pre>{error_detail}</pre>
        
        <hr>
        <h3>Troubleshooting Steps:</h3>
        <ol>
            <li><a href="/clear_cache">Clear token cache</a></li>
            <li><a href="/logout">Logout</a></li>
            <li>Login again</li>
            <li>Check Azure Portal for Mail.Read permission with admin consent</li>
        </ol>
        <p><a href="/">Home</a></p>
        """
   
if __name__ == "__main__":                  
   app.run(host="0.0.0.0", port=8000, debug=True)