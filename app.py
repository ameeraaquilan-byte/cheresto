from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
import sqlite3
import hashlib
import os
import json
from datetime import datetime, timedelta, date
import calendar
import base64
import uuid
import itertools
import re
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
        now = datetime.now()
        month_start = now.strftime('%Y-%m-01')
        c.execute("SELECT items FROM orders WHERE payment_status='Paid' AND order_date >= ?", (month_start,))
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

def is_duplicate_menu_item(name=None, image_url=None, exclude_id=None):
    conn = get_db()
    c = conn.cursor()
    name_exists = False
    if name:
        if exclude_id:
            c.execute("SELECT id FROM menu WHERE LOWER(name)=LOWER(?) AND id != ? AND available=1", (name, exclude_id))
        else:
            c.execute("SELECT id FROM menu WHERE LOWER(name)=LOWER(?) AND available=1", (name,))
        name_exists = c.fetchone() is not None
    image_exists = False
    if image_url and image_url.strip() and len(image_url.strip()) <= 8:
        if exclude_id:
            c.execute("SELECT id FROM menu WHERE image_url=? AND id != ? AND available=1", (image_url, exclude_id))
        else:
            c.execute("SELECT id FROM menu WHERE image_url=? AND available=1", (image_url,))
        image_exists = c.fetchone() is not None
    conn.close()
    return name_exists, image_exists

def is_duplicate_staff_name(name, exclude_id=None):
    conn = get_db()
    c = conn.cursor()
    if exclude_id:
        c.execute("SELECT id FROM users WHERE LOWER(name)=LOWER(?) AND role != 'customer' AND id != ?", (name, exclude_id))
    else:
        c.execute("SELECT id FROM users WHERE LOWER(name)=LOWER(?) AND role != 'customer'", (name,))
    exists = c.fetchone() is not None
    conn.close()
    return exists

def get_month_start():
    now = datetime.now()
    return now.strftime('%Y-%m-01')

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
        available INTEGER NOT NULL DEFAULT 1,
        stock INTEGER NOT NULL DEFAULT 100
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
        pickup_date TEXT,
        pickup_time TEXT,
        timestamp TEXT NOT NULL,
        notified INTEGER NOT NULL DEFAULT 0,
        cashier_approved INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        sender TEXT NOT NULL,
        message TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    migrations = [
        "ALTER TABLE menu ADD COLUMN available INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE menu ADD COLUMN stock INTEGER NOT NULL DEFAULT 100",
        "ALTER TABLE orders ADD COLUMN notified INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'Dine-in'",
        "ALTER TABLE orders ADD COLUMN display_id INTEGER",
        "ALTER TABLE orders ADD COLUMN order_date TEXT",
        "ALTER TABLE orders ADD COLUMN pickup_date TEXT",
        "ALTER TABLE orders ADD COLUMN pickup_time TEXT",
        "ALTER TABLE orders ADD COLUMN cashier_approved INTEGER NOT NULL DEFAULT 0",
    ]
    for m in migrations:
        try:
            c.execute(m)
            conn.commit()
        except:
            pass

    staff_defaults = [
        ('Admin', 'admin@chelicious.com', hash_password('admin123'), 'admin'),
        ('Cashier1', 'cashier@chelicious.com', hash_password('cash123'), 'cashier'),
        ('Kitchen1', 'kitchen@chelicious.com', hash_password('kitch123'), 'kitchen'),
        ('Waiter1', 'waiter@chelicious.com', hash_password('wait123'), 'waiter'),
    ]
    for name, email, pw, role in staff_defaults:
        c.execute("SELECT id FROM users WHERE email=?", (email,))
        if not c.fetchone():
            c.execute("INSERT INTO users (name,email,password,role) VALUES (?,?,?,?)", (name, email, pw, role))

    c.execute("SELECT COUNT(*) FROM menu")
    if c.fetchone()[0] == 0:
        menu_items = [
            ('Grilled Chicken','Food',185.00,'Juicy grilled chicken with herbs','🍗',1,50),
            ('Beef Burger','Food',210.00,'Classic beef patty with veggies','🍔',1,40),
            ('Spaghetti Bolognese','Food',175.00,'Rich meat sauce pasta','🍝',1,30),
            ('Crispy Pork Sisig','Food',165.00,'Filipino sizzling sisig','🥩',1,35),
            ('Chicken Adobo','Food',155.00,'Classic Filipino adobo','🍖',1,40),
            ('Pancit Canton','Food',140.00,'Stir-fried noodles','🍜',1,30),
            ('Fish & Chips','Food',195.00,'Crispy battered fish with fries','🐟',1,25),
            ('Caesar Salad','Food',130.00,'Fresh romaine with caesar dressing','🥗',1,20),
            ('Iced Coffee','Drinks',85.00,'Cold brew with milk','☕',1,100),
            ('Mango Shake','Drinks',95.00,'Fresh mango blended drink','🥭',1,60),
            ('Lemonade','Drinks',75.00,'Fresh squeezed lemon drink','🍋',1,80),
            ('Iced Tea','Drinks',65.00,'Classic sweet iced tea','🧋',1,100),
            ('Hot Chocolate','Drinks',80.00,'Rich creamy hot choco','🍫',1,60),
            ('Buko Juice','Drinks',70.00,'Fresh coconut juice','🥥',1,50),
            ('French Fries','Snacks',75.00,'Crispy golden fries','🍟',1,60),
            ('Onion Rings','Snacks',80.00,'Beer-battered onion rings','🧅',1,40),
            ('Spring Rolls','Snacks',85.00,'Crispy veggie spring rolls','🥚',1,40),
            ('Nachos','Snacks',110.00,'Loaded nachos with cheese','🌮',1,30),
            ('Chocolate Cake','Desserts',120.00,'Rich moist chocolate cake','🎂',1,20),
            ('Leche Flan','Desserts',95.00,'Classic Filipino custard','🍮',1,25),
            ('Halo-Halo','Desserts',110.00,'Mixed Filipino shaved ice dessert','🍨',1,30),
            ('Turon','Desserts',60.00,'Fried banana rolls with langka','🍌',1,35),
            ('Margherita Pizza','Pizza',245.00,'Classic tomato, mozzarella, fresh basil','🍕',1,20),
            ('Pepperoni Pizza','Pizza',265.00,'Loaded pepperoni with mozzarella','🍕',1,20),
            ('BBQ Chicken Pizza','Pizza',275.00,'Smoky BBQ sauce with grilled chicken','🍕',1,20),
            ('Hawaiian Pizza','Pizza',255.00,'Ham, pineapple, and mozzarella','🍕',1,15),
            ('Four Cheese Pizza','Pizza',285.00,'Mozzarella, cheddar, parmesan, gouda','🍕',1,15),
        ]
        c.executemany("INSERT INTO menu (name,category,price,description,image_url,available,stock) VALUES (?,?,?,?,?,?,?)", menu_items)

    conn.commit()
    conn.close()
    mine_rules()

def validate_gmail(email):
    pattern = r'^[a-zA-Z0-9._%+\-]+@gmail\.com$'
    return bool(re.match(pattern, email))

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/api/check-email', methods=['POST'])
def check_email():
    data = request.get_json()
    email = data.get('email', '').strip()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE email=?", (email,))
    exists = c.fetchone() is not None
    conn.close()
    return jsonify({'exists': exists}), 200

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
        if not validate_gmail(email):
            return jsonify({'error': 'Please enter a valid Gmail address (e.g. yourname@gmail.com). Only @gmail.com emails are accepted.'}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email=?", (email,))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'This email is already registered. Please login instead.'}), 400

        c.execute("SELECT id FROM users WHERE LOWER(name)=LOWER(?) AND role='customer'", (name,))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'This name is already taken. Please use a different name.'}), 400

        c.execute("INSERT INTO users (name,email,password,role) VALUES (?,?,?,?)",
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
        c.execute("SELECT * FROM users WHERE email=? AND password=?", (email, hash_password(password)))
        user = c.fetchone()
        conn.close()
        if not user:
            return jsonify({'error': 'Invalid email or password.'}), 401
        session['user_id'] = user['id']
        session['user_name'] = user['name']
        session['user_role'] = user['role']
        session.modified = True
        return jsonify({'message': 'Login successful!', 'user': {'id': user['id'], 'name': user['name'], 'role': user['role']}}), 200
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
    return jsonify({'user': {'id': session['user_id'], 'name': session['user_name'], 'role': session['user_role']}}), 200

@app.route('/api/menu', methods=['GET'])
def get_menu():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, name, category, price, description, image_url, available, stock FROM menu WHERE available=1 ORDER BY category, name")
        rows = c.fetchall()
        items = []
        for row in rows:
            d = dict(row)
            d['stock'] = 0 if d['stock'] <= 0 else 1
            items.append(d)
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
        return jsonify({'error': str(e)}), 500

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
                item['stock'] = 0 if item['stock'] <= 0 else 1
                bestsellers.append(item)
        conn.close()
        return jsonify({'bestsellers': bestsellers, 'min_orders': MIN_ORDERS}), 200
    except Exception as e:
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
        stock = int(data.get('stock', 100))

        if not name or not category or not price:
            return jsonify({'error': 'Name, category, and price are required.'}), 400
        if float(price) <= 0:
            return jsonify({'error': '❌ Price must be greater than 0.'}), 400
        if stock <= 0:
            return jsonify({'error': '❌ Stock quantity must be at least 1.'}), 400

        name_exists, image_exists = is_duplicate_menu_item(name=name, image_url=image_url)
        if name_exists:
            return jsonify({'error': f'❌ Menu item "{name}" already exists!'}), 400
        if image_exists:
            return jsonify({'error': '❌ This emoji is already used by another item!'}), 400

        image_path = image_url
        if image_url and image_url.startswith('data:image'):
            image_path = save_base64_image(image_url)

        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO menu (name,category,price,description,image_url,available,stock) VALUES (?,?,?,1,?)",
                  (name, category, float(price), description, image_path, stock))
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
            return jsonify({'message': 'Restored.'}), 200

        name = data.get('name', '').strip()
        category = data.get('category', '').strip()
        price = data.get('price')
        description = data.get('description', '').strip()
        image_url = data.get('image_url', '').strip()
        stock = int(data.get('stock', 0))

        if float(price) <= 0:
            return jsonify({'error': '❌ Price must be greater than 0.'}), 400
        if stock < 0:
            stock = 0

        name_exists, image_exists = is_duplicate_menu_item(name=name, image_url=image_url, exclude_id=item_id)
        if name_exists:
            return jsonify({'error': f'❌ Menu item "{name}" already exists!'}), 400
        if image_exists:
            return jsonify({'error': '❌ This emoji is already used by another item!'}), 400

        image_path = image_url
        if image_url and image_url.startswith('data:image'):
            image_path = save_base64_image(image_url)

        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE menu SET name=?,category=?,price=?,description=?,image_url=?,available=1,stock=? WHERE id=?",
                  (name, category, float(price), description, image_path, stock, item_id))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Updated.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/menu/<int:item_id>/stock', methods=['PUT'])
def update_stock(item_id):
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        data = request.get_json()
        action = data.get('action', 'set')
        amount = int(data.get('stock', 0))
        if amount <= 0:
            return jsonify({'error': '❌ Stock amount must be at least 1.'}), 400

        conn = get_db()
        c = conn.cursor()
        if action == 'add':
            c.execute("UPDATE menu SET stock = MAX(0, stock + ?) WHERE id=?", (amount, item_id))
        else:
            c.execute("UPDATE menu SET stock = MAX(0, ?) WHERE id=?", (amount, item_id))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Stock updated.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/menu/<int:item_id>', methods=['DELETE'])
def delete_menu_item(item_id):
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM menu WHERE id=?", (item_id,))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Deleted.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
    except:
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
            query = "SELECT * FROM menu WHERE available=1 AND stock > 0"
            if cart_names:
                query += f" AND name NOT IN ({placeholders})"
            query += " LIMIT 4"
            cur.execute(query, list(cart_names))
            recs = [dict(row) for row in cur.fetchall()]
        else:
            for name in list(suggestions)[:4]:
                cur.execute("SELECT * FROM menu WHERE name=? AND available=1 AND stock > 0", (name,))
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
        return jsonify({'error': 'Login required.'}), 401
    try:
        data = request.get_json()
        items = data.get('items')
        total_price = data.get('total_price')
        payment_method = data.get('payment_method', '').strip()
        order_type = data.get('order_type', 'Dine-in').strip()
        pickup_date = data.get('pickup_date', '').strip()
        pickup_time = data.get('pickup_time', '').strip()

        if not items or not total_price or not payment_method:
            return jsonify({'error': 'Order details incomplete.'}), 400

        now = datetime.now()
        if pickup_date:
            today_str = now.strftime('%Y-%m-%d')
            if pickup_date < today_str:
                return jsonify({'error': '❌ Pickup date cannot be in the past.'}), 400
            if pickup_date == today_str and pickup_time:
                current_minutes = now.hour * 60 + now.minute
                pickup_parts = pickup_time.split(':')
                pickup_minutes = int(pickup_parts[0]) * 60 + int(pickup_parts[1])
                if pickup_minutes < current_minutes + 5:
                    min_time = datetime(now.year, now.month, now.day, (current_minutes + 5) // 60, (current_minutes + 5) % 60)
                    return jsonify({'error': f'❌ Pickup time must be at least 5 minutes from now. Earliest: {min_time.strftime("%I:%M %p")}'}), 400

        conn = get_db()
        c = conn.cursor()
        for item in items:
            c.execute("SELECT stock, name FROM menu WHERE id=?", (item['id'],))
            row = c.fetchone()
            if row:
                real_stock = row['stock']
                if real_stock <= 0:
                    conn.close()
                    return jsonify({'error': f'❌ Sorry, {row["name"]} is out of stock.'}), 400
                if real_stock < item['qty']:
                    conn.close()
                    return jsonify({'error': f'❌ Only {real_stock} left in stock for {row["name"]}. Please reduce quantity.'}), 400

        for item in items:
            c.execute("UPDATE menu SET stock = MAX(0, stock - ?) WHERE id=?", (item['qty'], item['id']))

        today = now.strftime('%Y-%m-%d')
        timestamp = now.strftime('%Y-%m-%d %H:%M:%S')

        c.execute("SELECT COUNT(*) FROM orders WHERE order_date=?", (today,))
        count = c.fetchone()[0]
        display_id = count + 1

        c.execute("""INSERT INTO orders
                     (display_id, order_date, user_id, items, total_price, status,
                      payment_method, payment_status, order_type, pickup_date, pickup_time, timestamp, notified, cashier_approved)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0)""",
                  (display_id, today, session['user_id'], json.dumps(items),
                   float(total_price), 'Pending', payment_method, 'Unpaid',
                   order_type, pickup_date, pickup_time, timestamp))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Order placed!', 'order_id': display_id}), 201
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

        items = json.loads(order['items'])
        for item in items:
            c.execute("UPDATE menu SET stock = stock + ? WHERE id=?", (item['qty'], item['id']))

        c.execute("UPDATE orders SET status='Cancelled' WHERE display_id=? AND order_date=?", (display_id, today))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Order cancelled.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/clear-today', methods=['DELETE'])
def clear_today_orders():
    """Admin-only: delete all of today's orders and restore stock."""
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT items, status FROM orders WHERE order_date=?", (today,))
        rows = c.fetchall()
        for row in rows:
            if row['status'] != 'Cancelled':
                items = json.loads(row['items'])
                for item in items:
                    c.execute("UPDATE menu SET stock = stock + ? WHERE id=?", (item['qty'], item['id']))
        c.execute("DELETE FROM orders WHERE order_date=?", (today,))
        conn.commit()
        conn.close()
        return jsonify({'message': f"Today's orders cleared."}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/my', methods=['GET'])
def my_orders():
    if 'user_id' not in session:
        return jsonify({'error': 'Login required.'}), 401
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM orders WHERE user_id=? AND order_date=? ORDER BY display_id DESC", (session['user_id'], today))
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
        return jsonify({'error': str(e)}), 500

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
                     AND cashier_approved=1 AND notified=0 AND order_date=?""",
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
                         AND cashier_approved=1 AND notified=0 AND order_date=?""",
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
            c.execute("""SELECT o.*, u.name as customer_name, u.email as customer_email
                         FROM orders o JOIN users u ON o.user_id=u.id
                         ORDER BY o.id DESC LIMIT 200""")
        else:
            today = datetime.now().strftime('%Y-%m-%d')
            c.execute("""SELECT o.*, u.name as customer_name, u.email as customer_email
                         FROM orders o JOIN users u ON o.user_id=u.id
                         WHERE o.order_date=? ORDER BY o.display_id ASC""", (today,))
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
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/<int:display_id>/status', methods=['PUT'])
def update_order_status(display_id):
    if session.get('user_role') not in ('admin', 'kitchen', 'waiter', 'cashier'):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        data = request.get_json()
        new_status = data.get('status', '').strip()
        role = session.get('user_role')
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT * FROM orders WHERE display_id=? AND order_date=?", (display_id, today))
        order = c.fetchone()
        if not order:
            conn.close()
            return jsonify({'error': 'Order not found.'}), 404

        if role == 'kitchen':
            # Kitchen can ONLY move: Pending -> Preparing
            # Kitchen cannot mark Ready for Pickup — that is cashier's job
            if new_status != 'Preparing':
                conn.close()
                return jsonify({'error': 'Kitchen can only mark orders as Preparing.'}), 403
            if order['status'] != 'Pending':
                conn.close()
                return jsonify({'error': 'Only Pending orders can be started.'}), 400
            c.execute("UPDATE orders SET status=? WHERE display_id=? AND order_date=?",
                      (new_status, display_id, today))

        elif role == 'cashier':
            # Cashier can mark: Preparing -> Ready for Pickup (with cashier_approved=1, notified=0)
            # Cashier can also mark Completed after payment
            if new_status == 'Ready for Pickup':
                if order['status'] != 'Preparing':
                    conn.close()
                    return jsonify({'error': 'Order must be Preparing before marking Ready for Pickup.'}), 400
                c.execute("""UPDATE orders SET status='Ready for Pickup', cashier_approved=1, notified=0
                             WHERE display_id=? AND order_date=?""", (display_id, today))
            elif new_status == 'Completed':
                c.execute("UPDATE orders SET status=? WHERE display_id=? AND order_date=?",
                          (new_status, display_id, today))
                mine_rules()
            else:
                c.execute("UPDATE orders SET status=? WHERE display_id=? AND order_date=?",
                          (new_status, display_id, today))

        elif role == 'waiter':
            if new_status != 'Completed':
                conn.close()
                return jsonify({'error': 'Waiters can only mark Completed.'}), 403
            if order['status'] != 'Ready for Pickup' or not order['cashier_approved']:
                conn.close()
                return jsonify({'error': 'Order must be cashier-approved Ready for Pickup first.'}), 400
            c.execute("UPDATE orders SET status=? WHERE display_id=? AND order_date=?",
                      (new_status, display_id, today))

        else:
            # Admin
            c.execute("UPDATE orders SET status=? WHERE display_id=? AND order_date=?",
                      (new_status, display_id, today))
            if new_status == 'Completed':
                mine_rules()

        conn.commit()
        conn.close()
        return jsonify({'message': f'Status updated to {new_status}.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/<int:display_id>/cashier-notify', methods=['POST'])
def cashier_notify_customer(display_id):
    """Cashier triggers notification to customer that order is ready for pickup"""
    if session.get('user_role') not in ('admin', 'cashier'):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE orders SET cashier_approved=1, notified=0 WHERE display_id=? AND order_date=? AND status='Ready for Pickup'",
                  (display_id, today))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Customer has been notified that order is ready for pickup.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/<int:display_id>/cashier-approve', methods=['PUT'])
def cashier_approve(display_id):
    """Cashier collects cash and marks order as Paid"""
    if session.get('user_role') not in ('admin', 'cashier'):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE orders SET payment_status='Paid', cashier_approved=1, notified=0 WHERE display_id=? AND order_date=?",
                  (display_id, today))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Approved and marked paid.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        return jsonify({'message': 'Payment updated.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/orders/<int:display_id>/receipt', methods=['GET'])
def get_receipt(display_id):
    if session.get('user_role') not in ('admin', 'cashier'):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT o.*, u.name as customer_name FROM orders o
                     JOIN users u ON o.user_id=u.id
                     WHERE o.display_id=? AND o.order_date=?""", (display_id, today))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({'error': 'Order not found.'}), 404
        o = dict(row)
        if o['status'] == 'Cancelled':
            return jsonify({'error': '❌ Cannot print receipt for a cancelled order.'}), 400
        if o['payment_status'] == 'Unpaid':
            return jsonify({'error': '❌ Cannot print receipt for an unpaid order. Please collect payment first.'}), 400
        o['id'] = o['display_id']
        o['items'] = json.loads(o['items'])
        o['timestamp'] = format_timestamp(o['timestamp'])
        return jsonify({'receipt': o}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        return jsonify({'error': str(e)}), 500

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
        return jsonify({'error': str(e)}), 500

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
        return jsonify({'message': 'Role updated.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
            return jsonify({'error': 'Password must be 6-10 characters.'}), 400
        if not validate_gmail(email):
            return jsonify({'error': 'Email must be a valid @gmail.com address.'}), 400
        if role not in ('cashier', 'kitchen', 'waiter', 'admin'):
            return jsonify({'error': 'Invalid role.'}), 400

        if is_duplicate_staff_name(name):
            return jsonify({'error': f'❌ A staff member named "{name}" already exists. Please use a different name.'}), 400

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email=?", (email,))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'Email already registered.'}), 400
        c.execute("INSERT INTO users (name,email,password,role) VALUES (?,?,?,?)",
                  (name, email, hash_password(password), role))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Staff created.'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ═══════════════════════════════════════════════
# CHAT
# ═══════════════════════════════════════════════
@app.route('/api/chat/messages', methods=['GET'])
def get_chat_messages():
    if 'user_id' not in session:
        return jsonify({'error': 'Login required.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM chat_messages WHERE user_id=? ORDER BY id ASC", (session['user_id'],))
        msgs = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify({'messages': msgs}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/send', methods=['POST'])
def send_chat():
    if 'user_id' not in session:
        return jsonify({'error': 'Login required.'}), 401
    try:
        data = request.get_json()
        message = data.get('message', '').strip()
        if not message:
            return jsonify({'error': 'Message cannot be empty.'}), 400
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO chat_messages (user_id, sender, message, timestamp) VALUES (?,?,?,?)",
                  (session['user_id'], 'customer', message, now))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Sent.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/reply', methods=['POST'])
def chat_reply():
    if 'user_id' not in session:
        return jsonify({'error': 'Login required.'}), 401
    try:
        data = request.get_json()
        reply = data.get('reply', '').strip()
        target_user_id = data.get('user_id')
        if not reply or not target_user_id:
            return jsonify({'error': 'Missing fields.'}), 400
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_db()
        c = conn.cursor()
        c.execute("INSERT INTO chat_messages (user_id, sender, message, timestamp) VALUES (?,?,?,?)",
                  (target_user_id, 'admin', reply, now))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Reply sent.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/delete/<int:msg_id>', methods=['DELETE'])
def delete_chat_message(msg_id):
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM chat_messages WHERE id=?", (msg_id,))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Message deleted.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/conversation/<int:uid>', methods=['DELETE'])
def delete_conversation(uid):
    """Admin-only: delete all messages in a conversation with a specific user."""
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM chat_messages WHERE user_id=?", (uid,))
        conn.commit()
        conn.close()
        return jsonify({'message': 'Conversation deleted.'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/all-threads', methods=['GET'])
def all_chat_threads():
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""SELECT DISTINCT cm.user_id, u.name, u.email,
                     (SELECT message FROM chat_messages WHERE user_id=cm.user_id ORDER BY id DESC LIMIT 1) as last_msg,
                     (SELECT timestamp FROM chat_messages WHERE user_id=cm.user_id ORDER BY id DESC LIMIT 1) as last_time
                     FROM chat_messages cm JOIN users u ON cm.user_id=u.id
                     ORDER BY last_time DESC""")
        threads = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify({'threads': threads}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chat/thread/<int:uid>', methods=['GET'])
def get_thread(uid):
    if session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM chat_messages WHERE user_id=? ORDER BY id ASC", (uid,))
        msgs = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify({'messages': msgs}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ═══════════════════════════════════════════════
# SALES REPORT
# ═══════════════════════════════════════════════
@app.route('/api/reports/sales', methods=['GET'])
def sales_report():
    if session.get('user_role') not in ('admin', 'cashier'):
        return jsonify({'error': 'Unauthorized.'}), 401
    try:
        conn = get_db()
        c = conn.cursor()
        date_param = request.args.get('date') or datetime.now().strftime('%Y-%m-%d')
        try:
            datetime.strptime(date_param, '%Y-%m-%d')
        except:
            return jsonify({'error': 'Invalid date.'}), 400

        c.execute("""SELECT COUNT(*) as total_orders, COALESCE(SUM(total_price),0) as total_sales
                     FROM orders WHERE payment_status='Paid'
                     AND (order_date=? OR (order_date IS NULL AND DATE(timestamp)=?))""", (date_param, date_param))
        summary = dict(c.fetchone())

        c.execute("""SELECT payment_method, COUNT(*) as count, COALESCE(SUM(total_price),0) as total
                     FROM orders WHERE payment_status='Paid'
                     AND (order_date=? OR (order_date IS NULL AND DATE(timestamp)=?))
                     GROUP BY payment_method""", (date_param, date_param))
        by_method = [dict(r) for r in c.fetchall()]

        c.execute("""SELECT items FROM orders WHERE payment_status='Paid'
                     AND (order_date=? OR (order_date IS NULL AND DATE(timestamp)=?))""", (date_param, date_param))
        item_counts = {}
        item_revenue = {}
        for row in c.fetchall():
            try:
                items = json.loads(row['items'])
            except:
                continue
            for it in items:
                name = it.get('name')
                if not name: continue
                qty = it.get('qty', 1)
                price = it.get('price', 0)
                item_counts[name] = item_counts.get(name, 0) + qty
                item_revenue[name] = item_revenue.get(name, 0) + (qty * price)

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
        top_items = sorted([
            {'name': k, 'qty': item_counts[k], 'revenue': round(item_revenue[k], 2),
             'is_bestseller': alltime_counts.get(k, 0) >= MIN_BESTSELLER, 'alltime_qty': alltime_counts.get(k, 0)}
            for k in item_counts
        ], key=lambda x: x['qty'], reverse=True)

        past_days = []
        base_date = datetime.strptime(date_param, '%Y-%m-%d')
        for i in range(6, -1, -1):
            d = (base_date - timedelta(days=i)).strftime('%Y-%m-%d')
            c.execute("""SELECT COALESCE(SUM(total_price),0) as total, COUNT(*) as orders
                         FROM orders WHERE payment_status='Paid'
                         AND (order_date=? OR (order_date IS NULL AND DATE(timestamp)=?))""", (d, d))
            row = dict(c.fetchone())
            past_days.append({'date': d, 'total': float(row['total']), 'orders': row['orders']})

        conn.close()
        return jsonify({'date': date_param, 'summary': summary, 'by_payment_method': by_method,
                        'top_items': top_items, 'past_days': past_days, 'min_orders': MIN_BESTSELLER}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    init_db()
    app.run(debug=True, port=5000)
