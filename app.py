from flask import Flask, request, jsonify
import requests
from base64 import b64encode
from datetime import datetime
import json

app = Flask(__name__)

CONSUMER_KEY = 'nBZPStriomoXOJiaMfsud5E6D0GxgwVLcqwu5j4DJFK6EDVJ' 
CONSUMER_SECRET = 'xVVJK5NWAIn5QBbPTwMIwxA2nsTEpJjmLwuM5GrQI7jtPcDAhEBfrUxwO5X7gfYz'
SHORTCODE = '174379'  
PASSKEY = 'bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919'
CALLBACK_URL = 'https://example.com/callback'  # We'll update this after deployment

# 1. Function to get OAuth Access Token from Daraja
def get_access_token():
    try:
        auth_url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
        # Combine and encode credentials
        data = f"{CONSUMER_KEY}:{CONSUMER_SECRET}"
        encoded = b64encode(data.encode()).decode('utf-8')
        headers = {"Authorization": f"Basic {encoded}"}
        
        print("Requesting access token...")
        response = requests.get(auth_url, headers=headers, timeout=30)
        response.raise_for_status()  # Raises an error for bad status codes (4xx or 5xx)
        
        access_token = response.json().get('access_token')
        print("Access token received successfully.")
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting access token: {e}")
        return None


# 2. Main Endpoint for ESP32 to call
@app.route('/initiate-stk', methods=['POST'])
def initiate_stk():
    print("\n" + "="*50)
    print("Received a request from ESP32")
    print("="*50)
    
    try:
        # Get data from ESP32
        data_from_esp32 = request.get_json()
        if not data_from_esp32:
            return jsonify({'error': 'No data received'}), 400
            
        phone = data_from_esp32.get('phone')
        amount = data_from_esp32.get('amount')
        
        print(f"Initiating STK push for phone: {phone}, amount: {amount}")

        # Format phone number (e.g., 07... -> 2547...)
        if phone.startswith('0'):
            phone = '254' + phone[1:]
        print(f"Formatted phone number: {phone}")

        # Get access token
        access_token = get_access_token()
        if not access_token:
            return jsonify({'error': 'Authentication failed'}), 500

        # Create STK Push request
        endpoint = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

        # Generate timestamp and password
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password_str = f"{SHORTCODE}{PASSKEY}{timestamp}"
        password = b64encode(password_str.encode()).decode('utf-8')

        payload = {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": phone,
            "PartyB": SHORTCODE,
            "PhoneNumber": phone,
            "CallBackURL": f"{CALLBACK_URL}",
            "AccountReference": "PrepaidToken",
            "TransactionDesc": "Electricity Token"
        }

        print("Sending request to Daraja...")
        # Send request to Daraja
        response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        response_data = response.json()
        print(f"Daraja response: {json.dumps(response_data, indent=2)}")
        
        # Check if Daraja accepted the request
        if response_data.get('ResponseCode') == '0':
            print("✅ STK Push initiated successfully!")
            return jsonify({
                'message': 'STK Push sent to phone successfully!',
                'CheckoutRequestID': response_data.get('CheckoutRequestID')
            })
        else:
            print("❌ Failed to initiate STK Push.")
            return jsonify({'error': 'Failed to initiate payment', 'details': response_data}), 400

    except Exception as e:
        print(f"🔥 A critical error occurred: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# 3. Endpoint for Daraja's Callback
@app.route('/callback', methods=['POST'])
def callback():
    print("\n" + ">"*50)
    print("Received CALLBACK from Daraja!")
    print(">"*50)
    result = request.get_json()
    # Pretty print the entire callback result for debugging
    print(json.dumps(result, indent=2))
    
    # Check if the transaction was completed successfully
    if result.get('Body', {}).get('stkCallback', {}).get('ResultCode') == 0:
        print("🎉 Payment was successful!")
        # Extract metadata (Note: Amount is in the callback, not the token)
        callback_metadata = result['Body']['stkCallback']['CallbackMetadata']['Item']
        amount = next(item['Value'] for item in callback_metadata if item['Name'] == 'Amount')
        mpesa_receipt_number = next(item['Value'] for item in callback_metadata if item['Name'] == 'MpesaReceiptNumber')
        phone_number = next(item['Value'] for item in callback_metadata if item['Name'] == 'PhoneNumber')
        
        print(f"Amount: {amount}")
        print(f"MPESA Receipt: {mpesa_receipt_number}")
        print(f"Phone: {phone_number}")
        
        # !!! - IMPORTANT - !!!
        # Here is where you would generate your electricity token.
        # You would then store it (in a database) and link it to this transaction.
        # You could then send it back to the ESP32 via SMS or wait for the ESP32 to ask for it.
        generated_token = f"TOKEN-{mpesa_receipt_number}" # Placeholder logic
        
        print(f"Generated Token: {generated_token}")
        
    else:
        error_msg = result.get('Body', {}).get('stkCallback', {}).get('ResultDesc', 'Unknown error')
        print(f"❌ Payment failed: {error_msg}")
    
    # You must always acknowledge the callback to Daraja
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})

# Root endpoint to check if server is live
@app.route('/')
def home():
    return jsonify({"message": "MPESA ESP32 Server is running!"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)