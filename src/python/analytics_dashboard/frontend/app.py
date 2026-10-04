import os
import yaml
import json
from flask import Flask, render_template, request, session, redirect, url_for, send_from_directory, jsonify
import requests
import uuid
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'super_secret_key_for_demo'

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
PERSONAS_PATH = os.path.join(REPO_ROOT, 'data', 'personas', 'personas.yaml')
IMAGES_PATH = os.path.join(REPO_ROOT, 'data', 'personas', 'images')
DATA_DIR = os.path.join(BASE_DIR, 'report_history')

AGENT_API_URL = "http://localhost:8000"

def load_stores():
    ref_path = os.path.join(REPO_ROOT, 'data', 'database', 'reference_data.json')
    with open(ref_path, 'r') as f:
        data = json.load(f)
        
    stores = []
    # Add a Super Manager option
    stores.append({
        "id": "00000000-0000-0000-0000-000000000000",
        "name": "Headquarters (CEO)",
        "role": "Global Access",
        "department": "Executive",
        "photo_name": "kayo-miwa.png"
    })
    
    for store_name, store_data in data.get('stores', {}).items():
        stores.append({
            "id": store_data["rls_user_id"],
            "name": store_name,
            "role": "Store Manager",
            "department": "Retail Operations",
            "photo_name": "placeholder"
        })
        
    return stores

@app.route('/images/<path:filename>')
def serve_image(filename):
    return send_from_directory(IMAGES_PATH, filename)

@app.route('/')
def login():
    stores = load_stores()
    return render_template('login.html', stores=stores)

@app.route('/do_login', methods=['POST'])
def do_login():
    store_name = request.form.get('store_name')
    if store_name:
        stores = load_stores()
        selected = next((s for s in stores if s['name'] == store_name), None)
        if selected:
            session['user_id'] = selected['id']  # The actual RLS UUID
            session['user_name'] = selected['name']
        else:
            # Fallback
            session['user_id'] = store_name.replace(" ", "_").lower()
            session['user_name'] = store_name
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user_id = session['user_id']
    user_data_dir = os.path.join(DATA_DIR, user_id)
    
    # Load past reports
    reports = []
    if os.path.exists(user_data_dir):
        for d in os.listdir(user_data_dir):
            report_path = os.path.join(user_data_dir, d, 'report.json')
            if os.path.exists(report_path):
                with open(report_path, 'r') as f:
                    try:
                        report_data = json.load(f)
                        reports.append({
                            'id': d,
                            'title': report_data.get('title', report_data.get('prompt', 'Unknown Report')),
                            'summary': report_data.get('summary', report_data.get('prompt', '')),
                            'prompt': report_data.get('prompt', 'Unknown Prompt'),
                            'timestamp': report_data.get('timestamp', 'Unknown Time')
                        })
                    except Exception:
                        pass
    
    # Sort reports newest first
    reports.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Check if a specific report is requested
    report_id = request.args.get('report_id')
    current_report = None
    if report_id:
        report_path = os.path.join(user_data_dir, report_id, 'report.json')
        if os.path.exists(report_path):
            with open(report_path, 'r') as f:
                current_report = json.load(f)

    return render_template('dashboard.html', 
                           user_name=session.get('user_name'),
                           reports=reports,
                           current_report=current_report)

@app.route('/generate_report', methods=['POST'])
def generate_report():
    if 'user_id' not in session:
        return jsonify({"error": "Not logged in"}), 401
        
    messages = request.json.get('messages', [])
    user_name = session.get('user_name')
    user_id = session.get('user_id')
    
    # Call the Agent Microservice
    try:
        response = requests.post(f"{AGENT_API_URL}/generate", json={
            "messages": messages,
            "persona": user_name,
            "user_id": user_id
        })
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            return jsonify({"error": data["error"]}), 400
        return jsonify(data)
    except Exception as e:
        print(f"Error calling agent API: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/check_status/<task_id>')
def check_status(task_id):
    try:
        response = requests.get(f"{AGENT_API_URL}/status/{task_id}")
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(port=5000, debug=True)
