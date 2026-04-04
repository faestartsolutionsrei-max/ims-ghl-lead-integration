import os
import json
import anthropic
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
GHL_API_KEY = os.environ.get('GHL_API_KEY')
GHL_LOCATION_ID = os.environ.get('GHL_LOCATION_ID')

def extract_lead_data(email_body):
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

def create_ghl_contact(lead_data):
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

@app.route('/new-lead', methods=['POST'])
def handle_new_lead():
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
        print(f"ERROR: {str(e)}")
        print(f"TRACEBACK: {error_details}")
        return jsonify({'error': str(e), 'details': error_details}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'running'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
