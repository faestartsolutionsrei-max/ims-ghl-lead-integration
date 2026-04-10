import os
import json
import time
import threading
import anthropic
import requests
from flask import Flask, request, jsonify
from datetime import datetime, timezone
 
app = Flask(__name__)
 
# --- Environment Variables ---
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
GHL_API_KEY = os.environ.get('GHL_API_KEY')
GHL_LOCATION_ID = os.environ.get('GHL_LOCATION_ID')
AZURE_CLIENT_ID = os.environ.get('AZURE_CLIENT_ID')
AZURE_TENANT_ID = os.environ.get('AZURE_TENANT_ID')
AZURE_CLIENT_SECRET = os.environ.get('AZURE_CLIENT_SECRET')
MAILBOX = 'fae@investormortgagesolutions.com'
POLL_INTERVAL_SECONDS = 900  # 15 minutes
 
# --- In-memory set to track processed email IDs ---
# Prevents duplicate GHL contacts if the same email is seen across poll cycles
processed_email_ids = set()
 
 
# -------------------------------------------------------
# MICROSOFT GRAPH AUTH
# -------------------------------------------------------
 
def get_graph_token():
    """
    Fetches a fresh OAuth2 access token from Microsoft Graph
    using client credentials flow (app-only, no user login required).
    """
    url = f'https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token'
    data = {
        'grant_type': 'client_credentials',
        'client_id': AZURE_CLIENT_ID,
        'client_secret': AZURE_CLIENT_SECRET,
        'scope': 'https://graph.microsoft.com/.default'
    }
    response = requests.post(url, data=data)
    response.raise_for_status()
    return response.json().get('access_token')
 
 
# -------------------------------------------------------
# GRAPH EMAIL POLLING
# -------------------------------------------------------
 
def fetch_biggerpockets_emails(token):
    """
    Searches the Inbox for unread emails from Bryan Martinez
    containing 'New lead from BiggerPockets' in the subject.
    Returns a list of matching messages.
    """
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
 
    # Graph API filter: from Bryan, subject contains BP phrase, unread only
    filter_query = (
        "from/emailAddress/address eq 'bryan.martinez@investormortgagesolutions.com'"
        " and contains(subject, 'New lead from BiggerPockets')"
        " and isRead eq false"
    )
 
    url = (
        f'https://graph.microsoft.com/v1.0/users/{MAILBOX}/mailFolders/Inbox/messages'
        f'?$filter={requests.utils.quote(filter_query)}'
        f'&$select=id,subject,body,receivedDateTime,isRead'
        f'&$top=25'
    )
 
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json().get('value', [])
 
 
def mark_email_as_read(token, message_id):
    """
    Marks an email as read after processing so it won't be picked up again.
    The email stays in the Inbox — nothing is moved or deleted.
    """
    url = f'https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages/{message_id}'
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    requests.patch(url, headers=headers, json={'isRead': True})
 
 
# -------------------------------------------------------
# LEAD EXTRACTION (ANTHROPIC)
# -------------------------------------------------------
 
def extract_lead_data(email_body):
    """
    Sends the email body to Claude and extracts structured lead data as JSON.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model='claude-sonnet-4-20250514',
        max_tokens=1000,
        messages=[{
            'role': 'user',
            'content': f'''You are a mortgage lead intake assistant for Investor Mortgage Solutions.
Extract the following information from this BiggerPockets lead email and return
ONLY a JSON object with no extra text, no markdown, no code blocks:
{{
    "first_name": "", "last_name": "", "email": "", "phone": "",
    "loan_type": "", "property_type": "", "loan_amount": "",
    "purchase_price": "", "property_state": "", "timeline": "",
    "credit_score": "", "experience_level": "", "notes": "",
    "lead_temperature": ""
}}
For loan_type: DSCR, Non-QM, Bridge, Conventional, or Unknown
For lead_temperature: Hot, Warm, or Cold
For experience_level: First-Time, Experienced, or Seasoned
Email: {email_body}'''
        }]
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
 
 
# -------------------------------------------------------
# GHL CONTACT CREATION
# -------------------------------------------------------
 
def create_ghl_contact(lead_data):
    """
    Creates a new contact in GoHighLevel with extracted lead data and tags.
    """
    url = 'https://services.leadconnectorhq.com/contacts/'
    headers = {
        'Authorization': f'Bearer {GHL_API_KEY}',
        'Content-Type': 'application/json',
        'Version': '2021-07-28'
    }
    tags = ['src - biggerpockets', 'new lead']
    for field in ['loan_type', 'lead_temperature', 'experience_level']:
        if lead_data.get(field):
            tags.append(lead_data[field])
 
    payload = {
        'firstName': lead_data.get('first_name', ''),
        'lastName': lead_data.get('last_name', ''),
        'email': lead_data.get('email', ''),
        'phone': lead_data.get('phone', ''),
        'locationId': GHL_LOCATION_ID,
        'tags': tags
    }
    response = requests.post(url, headers=headers, json=payload)
    return response.json()
 
 
# -------------------------------------------------------
# POLL LOOP (runs in background thread)
# -------------------------------------------------------
 
def poll_inbox():
    """
    Background thread that polls the Inbox every 15 minutes.
    For each unread BiggerPockets lead email:
      1. Extracts lead data via Claude
      2. Creates GHL contact
      3. Marks email as read (stays in Inbox)
      4. Records message ID to prevent duplicate processing
    """
    print(f'[Poller] Started. Polling every {POLL_INTERVAL_SECONDS}s.')
 
    while True:
        try:
            print(f'[Poller] Checking inbox at {datetime.now(timezone.utc).isoformat()}')
            token = get_graph_token()
            emails = fetch_biggerpockets_emails(token)
            print(f'[Poller] Found {len(emails)} unread BP lead(s).')
 
            for email in emails:
                message_id = email.get('id')
 
                # Skip if already processed this session
                if message_id in processed_email_ids:
                    print(f'[Poller] Skipping already-processed email: {message_id}')
                    continue
 
                subject = email.get('subject', '')
                body = email.get('body', {}).get('content', '')
                print(f'[Poller] Processing: {subject}')
 
                try:
                    lead_data = extract_lead_data(body)
                    ghl_response = create_ghl_contact(lead_data)
                    mark_email_as_read(token, message_id)
                    processed_email_ids.add(message_id)
                    print(f'[Poller] Contact created for {lead_data.get("first_name")} {lead_data.get("last_name")}')
                    print(f'[Poller] GHL response: {ghl_response}')
 
                except Exception as e:
                    print(f'[Poller] Error processing email {message_id}: {str(e)}')
                    # Continue to next email rather than crashing the whole loop
 
        except Exception as e:
            print(f'[Poller] Error during poll cycle: {str(e)}')
 
        time.sleep(POLL_INTERVAL_SECONDS)
 
 
# -------------------------------------------------------
# FLASK ROUTES
# -------------------------------------------------------
 
@app.route('/new-lead', methods=['POST'])
def handle_new_lead():
    """
    Manual trigger endpoint — still works if you ever want to POST an email body directly.
    """
    try:
        raw_data = request.get_data(as_text=True)
        raw_data = raw_data.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        data = json.loads(raw_data)
        email_body = data.get('email_body', '')
        if not email_body:
            return jsonify({'error': 'No email body provided'}), 400
        lead_data = extract_lead_data(email_body)
        ghl_response = create_ghl_contact(lead_data)
        return jsonify({'success': True, 'lead_data': lead_data, 'ghl_response': ghl_response}), 200
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f'ERROR: {str(e)}')
        print(f'TRACEBACK: {error_details}')
        return jsonify({'error': str(e), 'details': error_details}), 500
 
 
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'running',
        'processed_this_session': len(processed_email_ids),
        'poll_interval_seconds': POLL_INTERVAL_SECONDS
    }), 200
 
 
# -------------------------------------------------------
# STARTUP
# -------------------------------------------------------
 
# Start the background poller thread at module load time (works with gunicorn)
poller_thread = threading.Thread(target=poll_inbox, daemon=True)
poller_thread.start()
 
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
 
