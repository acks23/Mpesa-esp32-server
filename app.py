# ===== IMPORTS SECTION =====
from flask import Flask, request, jsonify
import requests
from base64 import b64encode
from datetime import datetime
import json
import firebase_admin  # <-- NEW IMPORT
from firebase_admin import credentials, firestore, db  # <-- NEW IMPORTS
import os  # <-- NEW IMPORT (to read environment variables)

app = Flask(__name__)

print("Initializing Firebase...")
try:
    # Check if already initialized to avoid error during reload
    if not firebase_admin._apps:
        # Get the credentials from the Render environment variable
        cred_json_string = os.environ.get('Firebase_Credentials')
        if cred_json_string:
            cred_json = json.loads(cred_json_string)
            cred = credentials.Certificate(cred_json)
            
            # Initialize Firebase Admin SDK
            firebase_admin.initialize_app(cred)
            
            # Get a reference to the Firestore database
            db_firestore = firestore.client()
            
            # Initialize for Realtime Database (REPLACE WITH YOUR ACTUAL URL)
            realtime_db_url = "https://token-loading-system-default-rtdb.europe-west1.firebasedatabase.app/"  # <-- REPLACE THIS!
            
            print("✅ Firebase initialized successfully!")
        else:
            print("⚠️  FIREBASE_CREDENTIALS environment variable not found.")
except Exception as e:
    print(f"❌ Firebase initialization failed: {e}")

# ===== MPESA CREDENTIALS =====
CONSUMER_KEY = 'nBZPStriomoXOJiaMfsud5E6D0GxgwVLcqwu5j4DJFK6EDVJ'
CONSUMER_SECRET = 'xVVJK5NWAIn5QBbPTwMIwxA2nsTEpJjmLwuM5GrQI7jtPcDAhEBfrUxwO5X7gfYz'
SHORTCODE = '174379'
PASSKEY = 'bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919'
CALLBACK_URL = 'https://token-recharge-server.onrender.com/callback'  # Your Render URL

# ===== HELPER FUNCTIONS =====
def get_access_token():
    try:
        auth_url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
        data = f"{CONSUMER_KEY}:{CONSUMER_SECRET}"
        encoded = b64encode(data.encode()).decode('utf-8')
        headers = {"Authorization": f"Basic {encoded}"}
        
        print("Requesting access token...")
        response = requests.get(auth_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        access_token = response.json().get('access_token')
        print("Access token received successfully.")
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting access token: {e}")
        return None

def generate_20_digit_token(mpesa_receipt, amount):
    """Generate a 20-digit token based on MPESA receipt and amount"""
    # Simple algorithm: Use part of receipt + amount padded to 20 digits
    token_base = f"{mpesa_receipt}{str(amount).zfill(6)}"
    # Ensure it's exactly 20 digits by padding with zeros if needed
    generated_token = token_base.ljust(20, '0')[:20]
    return generated_token

# ===== ROUTES =====

@app.route('/initiate-stk', methods=['POST'])
def initiate_stk():
    print("\n" + "="*50)
    print("Received a request from ESP32/Website")
    print("="*50)
    
    try:
        data_from_esp32 = request.get_json()
        if not data_from_esp32:
            return jsonify({'error': 'No data received'}), 400
            
        phone = data_from_esp32.get('phone')
        amount = data_from_esp32.get('amount')
        
        print(f"Initiating STK push for phone: {phone}, amount: {amount}")

        if phone.startswith('0'):
            phone = '254' + phone[1:]
        print(f"Formatted phone number: {phone}")

        access_token = get_access_token()
        if not access_token:
            return jsonify({'error': 'Authentication failed'}), 500

        endpoint = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

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
        response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
        response_data = response.json()
        print(f"Daraja response: {json.dumps(response_data, indent=2)}")
        
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

@app.route('/callback', methods=['POST'])
def callback():
    print("\n" + ">"*50)
    print("Received CALLBACK from Daraja!")
    print(">"*50)
    result = request.get_json()
    print(json.dumps(result, indent=2))

    try:
        # Check if the transaction was successful
        if result.get('Body', {}).get('stkCallback', {}).get('ResultCode') == 0:
            print("🎉 Payment was successful!")

            # Extract metadata from the callback
            callback_metadata = result['Body']['stkCallback']['CallbackMetadata']['Item']
            amount = next(item['Value'] for item in callback_metadata if item['Name'] == 'Amount')
            mpesa_receipt = next(item['Value'] for item in callback_metadata if item['Name'] == 'MpesaReceiptNumber')
            phone_number = next(item['Value'] for item in callback_metadata if item['Name'] == 'PhoneNumber')

            # Generate a 20-digit token
            generated_token = generate_20_digit_token(mpesa_receipt, amount)
            
            print(f"Amount: {amount}")
            print(f"MPESA Receipt: {mpesa_receipt}")
            print(f"Phone: {phone_number}")
            print(f"Generated Token: {generated_token}")

            # 1. Save to Firestore (Permanent Record)
            transaction_data = {
                'phone': phone_number,
                'amount': amount,
                'mpesa_receipt': mpesa_receipt,
                'token': generated_token,
                'status': 'paid',
                'timestamp': firestore.SERVER_TIMESTAMP
            }

            # Add a new document to the 'transactions' collection
            doc_ref = db_firestore.collection('transactions').document(mpesa_receipt)
            doc_ref.set(transaction_data)
            print("✅ Transaction saved to Firestore!")

            # 2. Send to Realtime Database (For ESP32) - This will trigger the ESP32 immediately
            try:
                # Get a reference to your Realtime Database
                ref = db.reference('/meters/meter_01')  # Change 'meter_01' to your meter ID if needed
                
                # Push the token data
                ref.set({
                    'token': generated_token,
                    'amount': amount,
                    'timestamp': {'.sv': 'timestamp'}  # Firebase server timestamp
                })
                print("✅ Token sent to Realtime Database for ESP32!")
            except Exception as rtdb_error:
                print(f"❌ Error sending to Realtime Database: {rtdb_error}")

        else:
            error_msg = result.get('Body', {}).get('stkCallback', {}).get('ResultDesc', 'Unknown error')
            print(f"❌ Payment failed: {error_msg}")

    except Exception as e:
        print(f"🔥 Error processing callback: {e}")

    # Always acknowledge the callback
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})

@app.route('/')
def home():
    return jsonify({"message": "MPESA ESP32 Server is running!"})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)