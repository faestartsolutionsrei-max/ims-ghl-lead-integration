from flask import Flask, request, jsonify
import anthropic
import requests
import json
import os

app = Flask(__name__)

# ── Configuration (pulled from environment variables) ──────────────────
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
GHL_API_KEY = os.environ.get('GHL_API_KEY')
GHL_LOCATION_ID = os.environ.get('GHL_LOCATION_ID')

# ── Claude Email Parser ─────────────────────────────────────────────────
def extract_lead_data(email_body):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model='claude-sonnet-4-20250514',
        max_tokens=1000,
        messages=[{
            'role': 'user',
            'content': f'''You are a mortgage lead intake assistant for Investor Mortgage Solutions.
            Extract the following information from this BiggerPockets lead email and return
            ONLY a JSON object with no extra text:
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
    return json.loads(response.content[0].text.strip())

# ── GHL Contact Creator ─────────────────────────────────────────────────
def create_ghl_contact(lead_data):
    url = 'https://services.leadconnectorhq.com/hooks/x231M82jhEl1i8EzRYUt/webhook-trigger/d0b5c7c5-ece8-455a-a5c1-a02883d3898b'
    headers = {'Authorization': f'Bearer {GHL_API_KEY}', 'Content-Type': 'application/json'}
    tags = ['src - biggerpockets', 'new lead']
    for field in ['loan_type', 'lead_temperature', 'experience_level']:
        if lead_data.get(field): tags.append(lead_data[field])
    payload = {
        'firstName': lead_data.get('first_name', ''),
        'lastName': lead_data.get('last_name', ''),
        'email': lead_data.get('email', ''),
        'phone': lead_data.get('phone', ''),
        'locationId': GHL_LOCATION_ID,
        'tags': tags,
        'customField': {
            'loan_type': lead_data.get('loan_type', ''),
            'property_type': lead_data.get('property_type', ''),
            'loan_amount': lead_data.get('loan_amount', ''),
            'property_state': lead_data.get('property_state', ''),
            'timeline': lead_data.get('timeline', ''),
            'credit_score': lead_data.get('credit_score', ''),
            'experience_level': lead_data.get('experience_level', ''),
            'lead_notes': lead_data.get('notes', '')
        }
    }
    return requests.post(url, headers=headers, json=payload).json()

# ── Webhook Endpoint ────────────────────────────────────────────────────
@app.route('/new-lead', methods=['POST'])
def handle_new_lead():
    try:
        data = request.json
        email_body = data.get('email_body', '')
        if not email_body:
            return jsonify({'error': 'No email body provided'}), 400
        lead_data = extract_lead_data(email_body)
        ghl_response = create_ghl_contact(lead_data)
        return jsonify({'success': True, 'lead_data': lead_data, 'ghl_response': ghl_response}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'running'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
