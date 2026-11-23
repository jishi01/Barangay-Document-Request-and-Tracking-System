import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'barangay_secret_key_prod'
DATABASE = 'barangay.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Create residents table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS residents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firstname TEXT NOT NULL,
            surname TEXT NOT NULL,
            address TEXT NOT NULL,
            contact TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'resident'
        )
    ''')
    
    # Create requests table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref_id TEXT UNIQUE NOT NULL,
            username TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            purpose TEXT NOT NULL,
            urgency TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Auto-migration: Ensure username column exists
    cursor.execute("PRAGMA table_info(requests)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'username' not in columns:
        if 'user_id' in columns:
            cursor.execute("ALTER TABLE requests RENAME COLUMN user_id TO username")
        else:
            cursor.execute("ALTER TABLE requests ADD COLUMN username TEXT NOT NULL DEFAULT ''")

    # Safe insert for admin account (Ignores if 'admin' username already exists)
    hashed_pw = generate_password_hash('admin123')
    cursor.execute('''
        INSERT OR IGNORE INTO residents (firstname, surname, address, contact, username, password, role)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ('System', 'Admin', 'Barangay Hall', '09000000000', 'admin', hashed_pw, 'admin'))
        
    conn.commit()
    conn.close()

# Data Structures Helpers
def get_fifo_queues():
    conn = get_db()
    urgent = conn.execute(
        "SELECT * FROM requests WHERE status = 'Pending' AND urgency = 'Urgent' ORDER BY id ASC"
    ).fetchall()
    standard = conn.execute(
        "SELECT * FROM requests WHERE status = 'Pending' AND urgency != 'Urgent' ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [dict(q) for q in urgent], [dict(q) for q in standard]

def get_sorted_residents(order='ASC'):
    conn = get_db()
    direction = 'DESC' if order == 'DESC' else 'ASC'
    residents = conn.execute(f"SELECT * FROM residents WHERE role != 'admin' ORDER BY surname {direction}").fetchall()
    conn.close()
    return [dict(r) for r in residents]

def get_linked_list_nodes():
    conn = get_db()
    requests_list = conn.execute("SELECT * FROM requests ORDER BY id ASC").fetchall()
    conn.close()
    
    nodes = []
    prev_ref = None
    for req in requests_list:
        node = dict(req)
        node['prev_ref'] = prev_ref
        node['next_ref'] = None
        if nodes:
            nodes[-1]['next_ref'] = node['ref_id']
        nodes.append(node)
        prev_ref = node['ref_id']
    return nodes

@app.route('/')
def index():
    if 'user' in session:
        if session['user']['role'] == 'admin':
            return redirect(url_for('admin_portal'))
        return redirect(url_for('resident_portal'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db()
        user = conn.execute('SELECT * FROM residents WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            user_dict = dict(user)
            fname = user_dict.get('firstname') or user_dict.get('first_name') or ''
            session['user'] = {
                'id': user_dict['id'],
                'username': user_dict['username'],
                'firstname': fname,
                'surname': user_dict.get('surname', ''),
                'role': user_dict['role']
            }
            flash('Logged in successfully!', 'success')
            if user_dict['role'] == 'admin':
                return redirect(url_for('admin_portal'))
            return redirect(url_for('resident_portal'))
        else:
            flash('Invalid username or password', 'error')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        firstname = request.form.get('firstname', '').strip()
        surname = request.form.get('surname', '').strip()
        address = request.form.get('address', '').strip()
        contact = request.form.get('contact', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            flash('Please fill in all required fields.', 'error')
            return render_template('register.html')
            
        if username.lower() == 'admin':
            flash('Username "admin" is reserved.', 'error')
            return render_template('register.html')
            
        hashed_pw = generate_password_hash(password)
        conn = get_db()
        try:
            conn.execute('''
                INSERT INTO residents (firstname, surname, address, contact, username, password, role)
                VALUES (?, ?, ?, ?, ?, ?, 'resident')
            ''', (firstname, surname, address, contact, username, hashed_pw))
            conn.commit()
            flash('Account created successfully! Please sign in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists.', 'error')
        finally:
            conn.close()
            
    return render_template('register.html')

@app.route('/resident', methods=['GET', 'POST'])
def resident_portal():
    if 'user' not in session or session['user'].get('role') != 'resident':
        flash('Please login as a resident.', 'error')
        return redirect(url_for('login'))
        
    user = session['user']
    conn = get_db()
    
    if request.method == 'POST':
        doc_type = request.form.get('doc_type', '').strip()
        purpose = request.form.get('purpose', '').strip()
        urgency = request.form.get('urgency', 'Standard').strip()
        
        # Calculate next Reference ID
        count_row = conn.execute('SELECT COUNT(*) FROM requests').fetchone()
        count = count_row[0] if count_row else 0
        ref_id = f"REQ-{1001 + count}"
        
        # Insert request linked to current user
        conn.execute('''
            INSERT INTO requests (ref_id, username, doc_type, purpose, urgency, status)
            VALUES (?, ?, ?, ?, ?, 'Pending')
        ''', (ref_id, user['username'], doc_type, purpose, urgency))
        conn.commit()
        
        flash(f'Request submitted! Reference ID: {ref_id}', 'success')
        return redirect(url_for('resident_portal'))
        
    # Query database and convert sqlite3.Row objects to python dicts
    my_requests_rows = conn.execute(
        'SELECT * FROM requests WHERE username = ? ORDER BY id DESC', 
        (user['username'],)
    ).fetchall()
    conn.close()
    
    urgent_queue, standard_queue = get_fifo_queues()
    requests_with_pos = []
    
    for req in my_requests_rows:
        r_dict = dict(req)
        if r_dict['status'] == 'Pending':
            if r_dict['urgency'] == 'Urgent':
                pos = next((i + 1 for i, q in enumerate(urgent_queue) if q['ref_id'] == r_dict['ref_id']), None)
                r_dict['queue_pos'] = f"Urgent #{pos}" if pos else '-'
            else:
                pos = next((i + 1 for i, q in enumerate(standard_queue) if q['ref_id'] == r_dict['ref_id']), None)
                r_dict['queue_pos'] = f"Standard #{pos}" if pos else '-'
        else:
            r_dict['queue_pos'] = '-'
        requests_with_pos.append(r_dict)
        
    return render_template('resident.html', user=user, requests=requests_with_pos)

@app.route('/admin')
def admin_portal():
    if 'user' not in session or session['user']['role'] != 'admin':
        flash('Admin access required.', 'error')
        return redirect(url_for('login'))
        
    order = request.args.get('sort', 'ASC')
    urgent_queue, standard_queue = get_fifo_queues()
    linked_nodes = get_linked_list_nodes()
    sorted_residents = get_sorted_residents(order)
    undo_stack = session.get('undo_stack', [])
    
    return render_template('admin.html', 
                           user=session['user'],
                           urgent_queue=urgent_queue,
                           standard_queue=standard_queue,
                           nodes=linked_nodes, 
                           residents=sorted_residents, 
                           undo_stack=undo_stack,
                           sort_order=order)

@app.route('/admin/dequeue/<string:queue_type>', methods=['POST'])
def dequeue_request(queue_type):
    if 'user' not in session or session['user']['role'] != 'admin':
        return redirect(url_for('login'))
        
    urgent_queue, standard_queue = get_fifo_queues()
    target_queue = urgent_queue if queue_type == 'urgent' else standard_queue

    if target_queue:
        next_req = target_queue[0]
        conn = get_db()
        conn.execute("UPDATE requests SET status = 'Completed' WHERE id = ?", (next_req['id'],))
        conn.commit()
        conn.close()
        
        # Push action to LIFO Undo Stack (matches keys used by undo_action)
        undo_stack = session.get('undo_stack', [])
        undo_stack.append({
            'action': 'DEQUEUE',
            'req_id': next_req['id'],
            'ref_id': next_req['ref_id'],
            'previous_status': 'Pending'
        })
        session['undo_stack'] = undo_stack
        flash(f"Completed {queue_type.capitalize()} request {next_req['ref_id']}.", "success")
    else:
        flash(f"No pending requests in the {queue_type} queue!", "info")
        
    return redirect(url_for('admin_portal'))

@app.route('/admin/undo', methods=['POST'])
def undo_action():
    if 'user' not in session or session['user']['role'] != 'admin':
        return redirect(url_for('login'))
        
    undo_stack = session.get('undo_stack', [])
    if undo_stack:
        last_action = undo_stack.pop() # LIFO Pop
        conn = get_db()
        conn.execute("UPDATE requests SET status = ? WHERE id = ?", (last_action['previous_status'], last_action['req_id']))
        conn.commit()
        conn.close()
        
        session['undo_stack'] = undo_stack
        flash(f"Undid last action for {last_action['ref_id']}.", "info")
    else:
        flash("Nothing to undo!", "info")
        
    return redirect(url_for('admin_portal'))

@app.route('/admin/cancel/<int:req_id>', methods=['POST'])
def remove_node(req_id):
    if 'user' not in session or session['user']['role'] != 'admin':
        return redirect(url_for('login'))
        
    conn = get_db()
    req = conn.execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()
    if req:
        conn.execute("UPDATE requests SET status = 'Cancelled' WHERE id = ?", (req_id,))
        conn.commit()
        
        # Push to LIFO Stack
        undo_stack = session.get('undo_stack', [])
        undo_stack.append({
            'action': 'CANCEL',
            'req_id': req['id'],
            'ref_id': req['ref_id'],
            'previous_status': req['status']
        })
        session['undo_stack'] = undo_stack
        flash(f"Cancelled request {req['ref_id']}.", "warning")
        
    conn.close()
    return redirect(url_for('admin_portal'))

@app.route('/track', methods=['GET', 'POST'])
def track():
    result_node = None
    searched_ref = None
    if request.method == 'POST':
        searched_ref = request.form.get('ref_id', '').strip().upper()
        nodes = get_linked_list_nodes()
        result_node = next((n for n in nodes if n['ref_id'].upper() == searched_ref), None)
        
    return render_template('track.html', result=result_node, searched_ref=searched_ref)

@app.route('/residents')
def residents_list():
    if 'user' not in session or session['user']['role'] != 'admin':
        return redirect(url_for('login'))
        
    order = request.args.get('sort', 'ASC')
    residents = get_sorted_residents(order)
    return render_template('residents.html', user=session['user'], residents=residents, sort_order=order)

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)