from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
import sqlite3
import hashlib
import os
import json
from datetime import datetime, timedelta
import base64
import uuid
import itertools
from collections import defaultdict

app = Flask(__name__, static_folder='static', static_url_path='')
app.secret_key = 'chelicious_secret_key_2024'
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
CORS(app, supports_credentials=True, origins=["http://localhost:5000", "http://127.0.0.1:5000"])

DB_PATH = 'chelicious.db'
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

RULES = {}

def mine_rules(min_support=0.05, min_confidence=0.3):
    global RULES
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT items FROM orders WHERE payment_status='Paid'")
        rows = c.fetchall()
        conn.close()

        if len(rows) < 5:
            RULES = {}
            return

        transactions = []
        for row in rows:
            items = json.loads(row['items'])
            names = frozenset(i['name'] for i in items if i.get('name'))
            if len(names) >= 2:
                transactions.append(names)

        n = len(transactions)
        if n == 0:
            RULES = {}
            return

        item_count = defaultdict(int)
        pair_count = defaultdict(int)

        for t in transactions:
            for item in t:
                item_count[item] += 1
            for pair in itertools.combinations(sorted(t), 2):
                pair_count[pair] += 1

        freq_items = {k for k, v in item_count.items() if v / n >= min_support}
        freq_pairs = {k: v for k, v in pair_count.items()
                      if v / n >= min_support and k[0] in freq_items and k[1] in freq_items}

        rules = defaultdict(list)
        for (a, b), count in freq_pairs.items():
            conf_ab = count / item_count[a] if item_count[a] else 0
            conf_ba = count / item_count[b] if item_count[b] else 0
            if conf_ab >= min_confidence:
                rules[frozenset([a])].append((b, round(conf_ab, 2)))
            if conf_ba >= min_confidence:
                rules[frozenset([b])].append((a, round(conf_ba, 2)))

        RULES = {k: [name for name, _ in sorted(v, key=lambda x: -x[1])[:3]]
                 for k, v in rules.items()}

    except Exception as e:
        print(f"[mine_rules] Warning: {e}")
        RULES = {}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def format_timestamp(ts_str):
    try:
        dt = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%b %d, %Y %I:%M %p')
    except:
        return ts_str
    
    # Add this function after the imports and before init_db()
def is_duplicate_menu_item(name=None, image_url=None, exclude_id=None):
    """Check if a menu item with same name or same image already exists"""
    conn = get_db()
    c = conn.cursor()
    
    # Check for same name
    if name:
        if exclude_id:
            c.execute("SELECT id FROM menu WHERE name = ? AND id != ? AND available=1", (name, exclude_id))
        else:
            c.execute("SELECT id FROM menu WHERE name = ? AND available=1", (name,))
        name_exists = c.fetchone() is not None
    else:
        name_exists = False
    
    # Check for same image/emoji
    image_exists = False
    if image_url and image_url.strip():
        if exclude_id:
            c.execute("SELECT id FROM menu WHERE image_url = ? AND id != ? AND available=1", (image_url, exclude_id))
        else:
            c.execute("SELECT id FROM menu WHERE image_url = ? AND available=1", (image_url,))
        image_exists = c.fetchone() is not None
    
    conn.close()
    return name_exists, image_exists

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'customer'
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS menu (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price REAL NOT NULL,
        description TEXT,
        image_url TEXT,
        available INTEGER NOT NULL DEFAULT 1
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        display_id INTEGER,
        order_date TEXT,
        user_id INTEGER NOT NULL,
        items TEXT NOT NULL,
        total_price REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending',
        payment_method TEXT NOT NULL,
        payment_status TEXT NOT NULL DEFAULT 'Unpaid',
        order_type TEXT NOT NULL DEFAULT 'Dine-in',
        timestamp TEXT NOT NULL,
        notified INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    for migration in [
        "ALTER TABLE menu ADD COLUMN available INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE orders ADD COLUMN notified INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'Dine-in'",
        "ALTER TABLE orders ADD COLUMN display_id INTEGER",
        "ALTER TABLE orders ADD COLUMN order_date TEXT",
    ]:
        try:
            c.execute(migration)
            conn.commit()
        except:
            pass

    c.execute("SELECT * FROM users WHERE email='admin@chelicious.com'")
    if not c.fetchone():
        c.execute("INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                  ('Admin', 'admin@chelicious.com', hash_password('admin123'), 'admin'))

    c.execute("SELECT * FROM users WHERE email='cashier@chelicious.com'")
    if not c.fetchone():
        c.execute("INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                  ('Cashier1', 'cashier@chelicious.com', hash_password('cash123'), 'cashier'))

    c.execute("SELECT * FROM users WHERE email='kitchen@chelicious.com'")
    if not c.fetchone():
        c.execute("INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                  ('Kitchen1', 'kitchen@chelicious.com', hash_password('kitch123'), 'kitchen'))

    c.execute("SELECT * FROM users WHERE email='waiter@chelicious.com'")
    if not c.fetchone():
        c.execute("INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                  ('Waiter1', 'waiter@chelicious.com', hash_password('wait123'), 'waiter'))

    c.execute("SELECT COUNT(*) FROM menu")
    if c.fetchone()[0] == 0:
        menu_items = [
            ('Grilled Chicken', 'Food', 185.00, 'Juicy grilled chicken with herbs', '🍗', 1),
            ('Beef Burger', 'Food', 210.00, 'Classic beef patty with veggies', '🍔', 1),
            ('Spaghetti Bolognese', 'Food', 175.00, 'Rich meat sauce pasta', '🍝', 1),
            ('Crispy Pork Sisig', 'Food', 165.00, 'Filipino sizzling sisig', '🥩', 1),
            ('Chicken Adobo', 'Food', 155.00, 'Classic Filipino adobo', '🍖', 1),
            ('Pancit Canton', 'Food', 140.00, 'Stir-fried noodles', '🍜', 1),
            ('Fish & Chips', 'Food', 195.00, 'Crispy battered fish with fries', '🐟', 1),
            ('Caesar Salad', 'Food', 130.00, 'Fresh romaine with caesar dressing', '🥗', 1),
            ('Iced Coffee', 'Drinks', 85.00, 'Cold brew with milk', '☕', 1),
            ('Mango Shake', 'Drinks', 95.00, 'Fresh mango blended drink', '🥭', 1),
            ('Lemonade', 'Drinks', 75.00, 'Fresh squeezed lemon drink', '🍋', 1),
            ('Iced Tea', 'Drinks', 65.00, 'Classic sweet iced tea', '🧋', 1),
            ('Hot Chocolate', 'Drinks', 80.00, 'Rich creamy hot choco', '🍫', 1),
            ('Buko Juice', 'Drinks', 70.00, 'Fresh coconut juice', '🥥', 1),
            ('French Fries', 'Snacks', 75.00, 'Crispy golden fries', '🍟', 1),
            ('Onion Rings', 'Snacks', 80.00, 'Beer-battered onion rings', '🧅', 1),
            ('Spring Rolls', 'Snacks', 85.00, 'Crispy veggie spring rolls', '🥚', 1),
            ('Nachos', 'Snacks', 110.00, 'Loaded nachos with cheese', '🌮', 1),
            ('Chocolate Cake', 'Desserts', 120.00, 'Rich moist chocolate cake', '🎂', 1),
            ('Leche Flan', 'Desserts', 95.00, 'Classic Filipino custard', '🍮', 1),
            ('Halo-Halo', 'Desserts', 110.00, 'Mixed Filipino shaved ice dessert', '🍨', 1),
            ('Turon', 'Desserts', 60.00, 'Fried banana rolls with langka', '🍌', 1),
            ('Margherita Pizza', 'Pizza', 245.00, 'Classic tomato, mozzarella, fresh basil', '🍕', 1),
            ('Pepperoni Pizza', 'Pizza', 265.00, 'Loaded pepperoni with mozzarella', '🍕', 1),
            ('BBQ Chicken Pizza', 'Pizza', 275.00, 'Smoky BBQ sauce with grilled chicken', '🍕', 1),
            ('Hawaiian Pizza', 'Pizza', 255.00, 'Ham, pineapple, and mozzarella', '🍕', 1),
            ('Four Cheese Pizza', 'Pizza', 285.00, 'Mozzarella, cheddar, parmesan, gouda', '🍕', 1),
        ]
        c.executemany("INSERT INTO menu (name, category, price, description, image_url, available) VALUES (?,?,?,?,?,?)", menu_items)

    conn.commit()
    conn.close()
    mine_rules()

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()

        if not name or not email or not password:
            return jsonify({'error': 'All fields are required.'}), 400
        if len(password) < 6 or len(password) > 10:
            return jsonify({'error': 'Password must be 6-10 characters only.'}), 400
        if not email.endswith('@gmail.com'):
            return jsonify({'error': 'Email must be a valid @gmail.com address.'}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email=?", (email,))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'Email already registered.'}), 400

        c.execute("INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                  (name, email, hash_password(password), 'customer'))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Registration successful! You can now login.'}), 201
    except Exception as e:
        return jsonify({'error': f'Registration failed: {str(e)}'}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()

        if not email or not password:
            return jsonify({'error': 'Email and password are required.'}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE email=? AND password=?",
                  (email, hash_password(password)))
        user = c.fetchone()
        conn.close()

        if not user:
            return jsonify({'error': 'Invalid email or password.'}), 401

        session['user_id'] = user['id']
        session['user_name'] = user['name']
        session['user_role'] = user['role']
        session.modified = True

        return jsonify({
            'message': 'Login successful!',
            'user': {'id': user['id'], 'name': user['name'], 'role': user['role']}
        }), 200
    except Exception as e:
        return jsonify({'error': f'Login failed: {str(e)}'}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'message': 'Logged out successfully.'}), 200

@app.route('/api/me', methods=['GET'])
def me():
    if 'user_id' not in session:
        return jsonify({'user': None}), 200
    return jsonify({'user': {
        'id': session['user_id'],
        'name': session['user_name'],
        'role': session['user_role']
    }}), 200

@app.route('/api/menu', methods=['GET'])
def get_menu():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM menu WHERE available=1 ORDER BY category, name")
        items = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify({'menu': items}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to load menu: {str(e)}'}), 500

@app.route('/api/menu/all', methods=['GET'])
def get_menu_all():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM menu ORDER BY category, name")
        items = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify({'menu': items}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to load menu: {str(e)}'}), 500

@app.route('/api/menu/bestsellers', methods=['GET'])
def get_bestsellers():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT items FROM orders WHERE payment_status='Paid'")
        rows = c.fetchall()

        item_qty = defaultdict(int)
        for row in rows:
            items = json.loads(row['items'])
            for item in items:
                name = item.get('name', '')
                qty = item.get('qty', 1)
                if name:
                    item_qty[name] += qty

        MIN_ORDERS = 10
        qualified = {name: qty for name, qty in item_qty.items() if qty >= MIN_ORDERS}

        if not qualified:
            conn.close()
            return jsonify({'bestsellers': [], 'min_orders': MIN_ORDERS}), 200

        top_names = sorted(qualified.keys(), key=lambda n: qualified[n], reverse=True)[:8]

        bestsellers = []
        for name in top_names:
            c.execute("SELECT * FROM menu WHERE name=? AND available=1", (name,))
            row = c.fetchone()
            if row:
                item = dict(row)
                item['total_orders'] = qualified[name]
                bestsellers.append(item)

        conn.close()
        return jsonify({'bestsellers': bestsellers, 'min_orders': MIN_ORDERS}), 200

    except Exception as e:
        print(f"Error in bestsellers: {e}")
        return jsonify({'bestsellers': []}), 200

@app.route('/api/menu', methods=['POST'])
def add_menu_item():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        category = data.get('category', '').strip()
        price = data.get('price')
        description = data.get('description', '').strip()
        image_url = data.get('image_url', '').strip()

        if not name or not category or not price:
            return jsonify({'error': 'Name, category, and price are required.'}), 400

        # Check for duplicates
        name_exists, image_exists = is_duplicate_menu_item(name=name, image_url=image_url)
        
        if name_exists:
            return jsonify({'error': f'❌ Menu item with name "{name}" already exists! Please use a different name.'}), 400
        if image_exists:
            return jsonify({'error': '❌ This image/emoji is already used by another menu item! Please use a different image or emoji.'}), 400

        image_path = image_url
        if image_url and image_url.startswith('data:image'):
            image_path = save_base64_image(image_url)

        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO menu (name, category, price, description, image_url, available) VALUES (?,?,?,?,?,1)",
                  (name, category, float(price), description, image_path))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Menu item added.'}), 201
    except Exception as e:
        return jsonify({'error': f'Failed to add menu item: {str(e)}'}), 500

@app.route('/api/menu/<int:item_id>', methods=['PUT'])
def update_menu_item(item_id):
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        data = request.get_json()

        if data.get('restore'):
            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE menu SET available=1 WHERE id=?", (item_id,))
            conn.commit()
            conn.close()
            return jsonify({'message': 'Menu item restored.'}), 200

        name = data.get('name', '').strip()
        category = data.get('category', '').strip()
        price = data.get('price')
        description = data.get('description', '').strip()
        image_url = data.get('image_url', '').strip()

        # Check for duplicates (excluding current item)
        name_exists, image_exists = is_duplicate_menu_item(name=name, image_url=image_url, exclude_id=item_id)
        
        if name_exists:
            return jsonify({'error': f'❌ Menu item with name "{name}" already exists! Please use a different name.'}), 400
        if image_exists:
            return jsonify({'error': '❌ This image/emoji is already used by another menu item! Please use a different image or emoji.'}), 400

        image_path = image_url
        if image_url and image_url.startswith('data:image'):
            image_path = save_base64_image(image_url)

        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE menu SET name=?, category=?, price=?, description=?, image_url=?, available=1 WHERE id=?",
                  (name, category, float(price), description, image_path, item_id))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Menu item updated.'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to update menu item: {str(e)}'}), 500

@app.route('/api/menu/<int:item_id>', methods=['DELETE'])
def delete_menu_item(item_id):
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        # Permanently delete the menu item
        c.execute("DELETE FROM menu WHERE id=?", (item_id,))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Menu item permanently deleted.'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to delete item: {str(e)}'}), 500

def save_base64_image(data_url):
    try:
        header, encoded = data_url.split(',', 1)
        ext = 'jpg'
        if 'png' in header: ext = 'png'
        elif 'gif' in header: ext = 'gif'
        elif 'webp' in header: ext = 'webp'
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        with open(filepath, 'wb') as f:
            f.write(base64.b64decode(encoded))
        return f'/uploads/{filename}'
    except Exception as e:
        return ''

@app.route('/api/menu/recommendations', methods=['POST'])
def get_recommendations():
    try:
        data = request.get_json()
        cart_names = set([name.strip() for name in data.get('item_names', [])])

        suggestions = set()

        for name in cart_names:
            key = frozenset([name])
            for suggested in RULES.get(key, []):
                if suggested and suggested not in cart_names:
                    suggestions.add(suggested)

        if len(cart_names) >= 2:
            for pair in itertools.combinations(sorted(cart_names), 2):
                key = frozenset(pair)
                for suggested in RULES.get(key, []):
                    if suggested and suggested not in cart_names:
                        suggestions.add(suggested)

        conn = get_db()
        cur = conn.cursor()
        recs = []

        if not suggestions:
            placeholders = ','.join('?' * len(cart_names)) if cart_names else "''"
            query = "SELECT * FROM menu WHERE available=1"
            if cart_names:
                query += f" AND name NOT IN ({placeholders})"
            query += " LIMIT 4"
            cur.execute(query, list(cart_names))
            recs = [dict(row) for row in cur.fetchall()]
        else:
            for name in list(suggestions)[:4]:
                cur.execute("SELECT * FROM menu WHERE name=? AND available=1", (name,))
                row = cur.fetchone()
                if row:
                    recs.append(dict(row))

        conn.close()
        return jsonify({'recommendations': recs}), 200

    except Exception as e:
        return jsonify({'recommendations': [], 'error': str(e)}), 200

@app.route('/api/orders', methods=['POST'])
def place_order():
    if 'user_id' not in session:
        return jsonify({'error': 'Login required to place orders.'}), 401
    try:
        data = request.get_json()
        items = data.get('items')
        total_price = data.get('total_price')
        payment_method = data.get('payment_method', '').strip()
        order_type = data.get('order_type', 'Dine-in').strip()

        if not items or not total_price or not payment_method:
            return jsonify({'error': 'Order details are incomplete.'}), 400

        payment_status = 'Unpaid'
        if payment_method in ('GCash', 'Card Payment'):
            payment_status = 'Paid'

        now = datetime.now()
        today = now.strftime('%Y-%m-%d')
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')

        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM orders WHERE order_date=?", (today,))
        count = c.fetchone()[0]
        display_id = count + 1

        c.execute("""INSERT INTO orders
                     (display_id, order_date, user_id, items, total_price, status,
                      payment_method, payment_status, order_type, timestamp, notified)
                     VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
                  (display_id, today, session['user_id'], json.dumps(items),
                   float(total_price), 'Pending', payment_method, payment_status,
                   order_type, timestamp))
        conn.commit()
        conn.close()

        if payment_status == 'Paid':
            mine_rules()

        return jsonify({'message': 'Order placed successfully!', 'order_id': display_id}), 201
    except Exception as e:
        return jsonify({'error': f'Failed to place order: {str(e)}'}), 500

@app.route('/api/orders/<int:display_id>/cancel', methods=['PUT'])
def cancel_order(display_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Login required.'}), 401
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM orders WHERE display_id=? AND order_date=?", (display_id, today))
        order = c.fetchone()
        if not order:
            conn.close()
            return jsonify({'error': 'Order not found.'}), 404

        role = session.get('user_role')
        user_id = session.get('user_id')

        if role == 'customer':
            if order['user_id'] != user_id:
                conn.close()
                return jsonify({'error': 'Not your order.'}), 403
            if order['status'] != 'Pending':
                conn.close()
                return jsonify({'error': 'Only Pending orders can be cancelled.'}), 400
        elif role not in ('admin', 'cashier'):
            conn.close()
            return jsonify({'error': 'Unauthorized.'}), 401

        c.execute("UPDATE orders SET status='Cancelled' WHERE display_id=? AND order_date=?",
                  (display_id, today))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Order cancelled.'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to cancel order: {str(e)}'}), 500

@app.route('/api/orders/my', methods=['GET'])
def my_orders():
    if 'user_id' not in session:
        return jsonify({'error': 'Login required.'}), 401
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT * FROM orders WHERE user_id=? AND order_date=?
                     ORDER BY display_id ASC""",
                  (session['user_id'], today))
        orders = []
        for row in c.fetchall():
            o = dict(row)
            o['id'] = o['display_id']
            o['items'] = json.loads(o['items'])
            o['timestamp'] = format_timestamp(o['timestamp'])
            orders.append(o)
        conn.close()
        return jsonify({'orders': orders}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to fetch orders: {str(e)}'}), 500

@app.route('/api/orders/notifications', methods=['GET'])
def get_notifications():
    if 'user_id' not in session:
        return jsonify({'notifications': []}), 200
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT id, display_id, items, total_price, timestamp FROM orders
                     WHERE user_id=? AND status='Ready for Pickup'
                     AND notified=0 AND order_date=?""",
                  (session['user_id'], today))
        notifs = []
        for row in c.fetchall():
            o = dict(row)
            o['id'] = o['display_id']
            o['items'] = json.loads(o['items'])
            o['timestamp'] = format_timestamp(o['timestamp'])
            notifs.append(o)
        if notifs:
            c.execute("""UPDATE orders SET notified=1
                         WHERE user_id=? AND status='Ready for Pickup'
                         AND notified=0 AND order_date=?""",
                      (session['user_id'], today))
            conn.commit()
        conn.close()
        return jsonify({'notifications': notifs}), 200
    except Exception as e:
        return jsonify({'notifications': []}), 200

@app.route('/api/orders/all', methods=['GET'])
def all_orders():
    if session.get('user_role') not in ('admin', 'cashier', 'kitchen', 'waiter'):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()

        if session.get('user_role') == 'admin':
            c.execute("""
                SELECT o.*, u.name as customer_name, u.email as customer_email
                FROM orders o
                JOIN users u ON o.user_id = u.id
                ORDER BY o.id DESC
                LIMIT 200
            """)
        else:
            today = datetime.now().strftime('%Y-%m-%d')
            c.execute("""
                SELECT o.*, u.name as customer_name, u.email as customer_email
                FROM orders o JOIN users u ON o.user_id = u.id
                WHERE o.order_date=?
                ORDER BY o.display_id ASC
            """, (today,))

        orders = []
        for row in c.fetchall():
            o = dict(row)
            o['id'] = o.get('display_id') or o['id']
            o['items'] = json.loads(o['items'])
            o['timestamp'] = format_timestamp(o['timestamp'])
            orders.append(o)
        conn.close()
        return jsonify({'orders': orders}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to fetch orders: {str(e)}'}), 500

@app.route('/api/orders/<int:display_id>/status', methods=['PUT'])
def update_order_status(display_id):
    if session.get('user_role') not in ('admin', 'kitchen', 'waiter'):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        data = request.get_json()
        new_status = data.get('status', '').strip()
        valid = ['Pending', 'Preparing', 'Ready for Pickup', 'Completed', 'Cancelled']
        if new_status not in valid:
            return jsonify({'error': 'Invalid status.'}), 400

        role = session.get('user_role')
        today = datetime.now().strftime('%Y-%m-%d')

        conn = get_db()
        c = conn.cursor()

        if role == 'waiter':
            if new_status != 'Completed':
                conn.close()
                return jsonify({'error': 'Waiters can only mark orders as Completed.'}), 403
            c.execute("SELECT status FROM orders WHERE display_id=? AND order_date=?",
                      (display_id, today))
            current = c.fetchone()
            if not current or current['status'] != 'Ready for Pickup':
                conn.close()
                return jsonify({'error': 'Order must be Ready for Pickup before marking Completed.'}), 400

        if new_status == 'Ready for Pickup':
            c.execute("UPDATE orders SET status=?, notified=0 WHERE display_id=? AND order_date=?",
                      (new_status, display_id, today))
        else:
            c.execute("UPDATE orders SET status=? WHERE display_id=? AND order_date=?",
                      (new_status, display_id, today))
        conn.commit()
        conn.close()
        return jsonify({'message': f'Order status updated to {new_status}.'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to update order status: {str(e)}'}), 500

@app.route('/api/orders/<int:display_id>/payment', methods=['PUT'])
def update_payment_status(display_id):
    if session.get('user_role') not in ('admin', 'cashier'):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        data = request.get_json()
        payment_status = data.get('payment_status', '').strip()
        if payment_status not in ('Paid', 'Unpaid'):
            return jsonify({'error': 'Invalid payment status.'}), 400

        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE orders SET payment_status=? WHERE display_id=? AND order_date=?",
                  (payment_status, display_id, today))
        conn.commit()
        conn.close()

        if payment_status == 'Paid':
            mine_rules()

        return jsonify({'message': 'Payment status updated.'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to update payment: {str(e)}'}), 500

@app.route('/api/orders/<int:display_id>/receipt', methods=['GET'])
def get_receipt(display_id):
    if session.get('user_role') not in ('admin', 'cashier'):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT o.*, u.name as customer_name, u.email as customer_email
                     FROM orders o JOIN users u ON o.user_id = u.id
                     WHERE o.display_id=? AND o.order_date=?""", (display_id, today))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'Order not found.'}), 404
        o = dict(row)
        o['id'] = o['display_id']
        o['items'] = json.loads(o['items'])
        o['timestamp'] = format_timestamp(o['timestamp'])
        return jsonify({'receipt': o}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to fetch receipt: {str(e)}'}), 500

@app.route('/api/users', methods=['GET'])
def get_users():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, name, email, role FROM users ORDER BY role, name")
        users = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify({'users': users}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to fetch users: {str(e)}'}), 500

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    if user_id == session.get('user_id'):
        return jsonify({'error': 'Cannot delete your own account.'}), 400
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({'message': 'User deleted.'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to delete user: {str(e)}'}), 500

@app.route('/api/users/<int:user_id>/role', methods=['PUT'])
def update_user_role(user_id):
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        data = request.get_json()
        new_role = data.get('role', '').strip()
        if new_role not in ('customer', 'cashier', 'kitchen', 'waiter', 'admin'):
            return jsonify({'error': 'Invalid role.'}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
        conn.commit()
        conn.close()
        return jsonify({'message': 'User role updated.'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to update user role: {str(e)}'}), 500
@app.route('/api/staff', methods=['POST'])
def add_staff():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        data = request.get_json()
        name = data.get('name', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()
        role = data.get('role', '').strip()

        if not name or not email or not password or not role:
            return jsonify({'error': 'All fields are required.'}), 400
        if len(password) < 6 or len(password) > 10:
            return jsonify({'error': 'Password must be 6-10 characters only.'}), 400
        if not email.endswith('@gmail.com'):
            return jsonify({'error': 'Email must be a valid @gmail.com address.'}), 400
        if role not in ('cashier', 'kitchen', 'waiter', 'admin'):
            return jsonify({'error': 'Invalid role.'}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email=?", (email,))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'Email already registered.'}), 400

        c.execute("INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)",
                  (name, email, hash_password(password), role))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Staff account created successfully.'}), 201
    except Exception as e:
        return jsonify({'error': f'Failed to create staff: {str(e)}'}), 500

# ===================================================
# SALES REPORT
#   - Shows ALL items sold (no min-order filter)
#   - Best Seller badge only for items >= 10 orders (all-time)
#   - Past 7 days revenue trend
#   - Fallback to DATE(timestamp) if order_date IS NULL (older orders)
# ===================================================
@app.route('/api/reports/sales', methods=['GET'])
def sales_report():
    if session.get('user_role') not in ('admin', 'cashier'):
        return jsonify({'error': 'Unauthorized.'}), 401

    try:
        conn = get_db()
        c = conn.cursor()

        date_param = request.args.get('date')
        if not date_param:
            date_param = datetime.now().strftime('%Y-%m-%d')

        try:
            datetime.strptime(date_param, '%Y-%m-%d')
        except:
            return jsonify({'error': 'Invalid date format'}), 400

        # Summary for selected date — fallback to DATE(timestamp) if order_date is NULL
        c.execute("""
            SELECT COUNT(*) as total_orders,
                   COALESCE(SUM(total_price), 0) as total_sales
            FROM orders
            WHERE payment_status='Paid'
            AND (order_date=? OR (order_date IS NULL AND DATE(timestamp)=?))
        """, (date_param, date_param))
        summary = dict(c.fetchone())

        # By payment method for selected date
        c.execute("""
            SELECT payment_method, COUNT(*) as count,
                   COALESCE(SUM(total_price),0) as total
            FROM orders
            WHERE payment_status='Paid'
            AND (order_date=? OR (order_date IS NULL AND DATE(timestamp)=?))
            GROUP BY payment_method
        """, (date_param, date_param))
        by_method = [dict(r) for r in c.fetchall()]

        # Items sold on selected date (ALL items, no min filter)
        c.execute("""
            SELECT items FROM orders
            WHERE payment_status='Paid'
            AND (order_date=? OR (order_date IS NULL AND DATE(timestamp)=?))
        """, (date_param, date_param))

        item_counts = {}
        item_revenue = {}
        for row in c.fetchall():
            try:
                items = json.loads(row['items'])
            except:
                continue
            for it in items:
                name = it.get('name')
                if not name:
                    continue
                qty = it.get('qty', 1)
                price = it.get('price', 0)
                item_counts[name] = item_counts.get(name, 0) + qty
                item_revenue[name] = item_revenue.get(name, 0) + (qty * price)

        # All-time qty for best seller badge
        c.execute("SELECT items FROM orders WHERE payment_status='Paid'")
        alltime_counts = {}
        for row in c.fetchall():
            try:
                items = json.loads(row['items'])
            except:
                continue
            for it in items:
                name = it.get('name')
                if name:
                    alltime_counts[name] = alltime_counts.get(name, 0) + it.get('qty', 1)

        MIN_BESTSELLER = 10

        # All items sold that day, sorted by qty desc
        top_items = sorted([
            {
                'name': k,
                'qty': item_counts[k],
                'revenue': round(item_revenue[k], 2),
                'is_bestseller': alltime_counts.get(k, 0) >= MIN_BESTSELLER,
                'alltime_qty': alltime_counts.get(k, 0)
            }
            for k in item_counts
        ], key=lambda x: x['qty'], reverse=True)

        # Past 7 days trend (ending on selected date) — fallback to DATE(timestamp)
        past_days = []
        base_date = datetime.strptime(date_param, '%Y-%m-%d')
        for i in range(6, -1, -1):
            d = (base_date - timedelta(days=i)).strftime('%Y-%m-%d')
            c.execute("""
                SELECT COALESCE(SUM(total_price),0) as total, COUNT(*) as orders
                FROM orders
                WHERE payment_status='Paid'
                AND (order_date=? OR (order_date IS NULL AND DATE(timestamp)=?))
            """, (d, d))
            row = dict(c.fetchone())
            past_days.append({
                'date': d,
                'total': float(row['total']),
                'orders': row['orders']
            })

        conn.close()

        return jsonify({
            'date': date_param,
            'summary': summary,
            'by_payment_method': by_method,
            'top_items': top_items,
            'past_days': past_days,
            'min_orders': MIN_BESTSELLER
        }), 200

    except Exception as e:
        return jsonify({'error': f'Report error: {str(e)}'}), 500


if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    init_db()
    app.run(debug=True, port=5000)