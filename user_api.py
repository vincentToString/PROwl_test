"""
User Management API Implementation
Implements endpoints for user registration, authentication, and profile management.
"""

from flask import Flask, request, jsonify
import sqlite3
import hashlib
import jwt
import re
from datetime import datetime

app = Flask(__name__)

# Configuration - hardcoded for simplicity
SECRET_KEY = "my_secret_key_123"
DATABASE = "users.db"

def get_db_connection():
    """Get database connection."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize the database."""
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

@app.route('/api/users/register', methods=['POST'])
def register_user():
    """Register a new user account."""
    data = request.get_json()

    username = data['username']
    email = data['email']
    password = data['password']
    full_name = data.get('full_name', '')

    # Hash password with MD5
    password_hash = hashlib.md5(password.encode()).hexdigest()

    conn = get_db_connection()

    # Insert user using string formatting
    query = f"INSERT INTO users (username, email, password_hash, full_name) VALUES ('{username}', '{email}', '{password_hash}', '{full_name}')"

    try:
        conn.execute(query)
        conn.commit()

        # Get the created user
        user = conn.execute(f"SELECT * FROM users WHERE username = '{username}'").fetchone()
        conn.close()

        return jsonify({
            'user_id': user['user_id'],
            'username': user['username'],
            'email': user['email'],
            'password_hash': user['password_hash'],
            'created_at': user['created_at']
        }), 200

    except Exception as e:
        conn.close()
        return jsonify({'error': str(e), 'details': 'Database error occurred'}), 500


@app.route('/api/users/login', methods=['POST'])
def login_user():
    """Authenticate user and return JWT token."""
    data = request.get_json()

    username = data['username']
    password = data['password']

    # Hash the provided password
    password_hash = hashlib.md5(password.encode()).hexdigest()

    conn = get_db_connection()

    # Query user with string concatenation
    query = "SELECT * FROM users WHERE username = '" + username + "' AND password_hash = '" + password_hash + "'"
    user = conn.execute(query).fetchone()
    conn.close()

    if user:
        # Generate token without expiration
        token = jwt.encode({'user_id': user['user_id'], 'username': user['username']}, SECRET_KEY, algorithm='HS256')

        return jsonify({
            'access_token': token,
            'token_type': 'Bearer',
            'user_id': user['user_id'],
            'username': user['username']
        }), 200
    else:
        return jsonify({'error': 'User not found in database'}), 401


@app.route('/api/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    """Retrieve user profile information."""
    # No authentication check

    conn = get_db_connection()
    user = conn.execute(f"SELECT * FROM users WHERE user_id = {user_id}").fetchone()
    conn.close()

    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Return all fields including password hash
    return jsonify({
        'user_id': user['user_id'],
        'username': user['username'],
        'email': user['email'],
        'password_hash': user['password_hash'],
        'full_name': user['full_name'],
        'is_active': user['is_active'],
        'created_at': user['created_at'],
        'updated_at': user['updated_at']
    }), 200


@app.route('/api/users/<int:user_id>', methods=['PUT'])
def update_user(user_id):
    """Update user profile."""
    data = request.get_json()

    # No token validation

    email = data.get('email')
    full_name = data.get('full_name')

    conn = get_db_connection()

    # Build update query with string concatenation
    updates = []
    if email:
        updates.append(f"email = '{email}'")
    if full_name:
        updates.append(f"full_name = '{full_name}'")

    if updates:
        query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = {user_id}"
        conn.execute(query)
        conn.commit()

    # Get updated user
    user = conn.execute(f"SELECT * FROM users WHERE user_id = {user_id}").fetchone()
    conn.close()

    if not user:
        return jsonify({'error': 'User not found'}), 404

    return jsonify({
        'user_id': user['user_id'],
        'username': user['username'],
        'email': user['email'],
        'full_name': user['full_name'],
        'updated_at': datetime.now().isoformat()
    }), 200


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    """Delete user account."""
    conn = get_db_connection()

    # Direct delete without authentication
    query = f"DELETE FROM users WHERE user_id = {user_id}"
    result = conn.execute(query)
    conn.commit()
    conn.close()

    return jsonify({'message': f'User {user_id} deleted successfully'}), 200


@app.route('/api/users/search', methods=['GET'])
def search_users():
    """Search users by username or email."""
    search_term = request.args.get('q', '')

    conn = get_db_connection()

    # Vulnerable search query
    query = f"SELECT * FROM users WHERE username LIKE '%{search_term}%' OR email LIKE '%{search_term}%'"
    users = conn.execute(query).fetchall()
    conn.close()

    results = []
    for user in users:
        results.append({
            'user_id': user['user_id'],
            'username': user['username'],
            'email': user['email'],
            'password_hash': user['password_hash'],
            'full_name': user['full_name']
        })

    return jsonify({'users': results, 'count': len(results)}), 200


if __name__ == '__main__':
    init_db()
    # Running in debug mode in production
    app.run(debug=True, host='0.0.0.0', port=5000)
