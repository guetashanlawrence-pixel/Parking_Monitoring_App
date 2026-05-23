from flask import Flask, render_template, request, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import math
import io
import csv
import os
from fpdf import FPDF

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super_secret_smartpark_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///smartpark.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class ParkingSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slot_number = db.Column(db.String(10), unique=True, nullable=False)
    slot_type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='Available')
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=True)


class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plate_number = db.Column(db.String(20), nullable=False)
    owner_name = db.Column(db.String(50), nullable=False)
    vehicle_type = db.Column(db.String(20), nullable=False)
    time_in = db.Column(db.DateTime, default=datetime.utcnow)
    time_out = db.Column(db.DateTime, nullable=True)
    fee = db.Column(db.Float, default=0.0)
    slot_id = db.Column(db.Integer, db.ForeignKey('parking_slot.id'), nullable=True)


def seed_database():
    if ParkingSlot.query.first() is None:
        slots_config = []
        for i in range(1, 11):
            slots_config.append((f'C-{i:02d}', 'Car'))
            slots_config.append((f'M-{i:02d}', 'Motorcycle'))
            slots_config.append((f'V-{i:02d}', 'Van'))
            slots_config.append((f'T-{i:02d}', 'Truck'))

        for slot_num, slot_type in slots_config:
            slot = ParkingSlot(slot_number=slot_num, slot_type=slot_type)
            db.session.add(slot)
        db.session.commit()


with app.app_context():
    db.create_all()
    seed_database()

CSV_FILE = 'parking_log.csv'
CSV_HEADERS = ['Timestamp', 'Event', 'Plate Number', 'Owner', 'Vehicle Type', 'Slot', 'Fee', 'Duration']


def log_to_csv(data_dict):
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists: writer.writeheader()
        writer.writerow(data_dict)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/slots', methods=['GET'])
def get_slots():
    slots = ParkingSlot.query.all()
    result = []
    for slot in slots:
        vehicle = Vehicle.query.get(slot.vehicle_id) if slot.vehicle_id else None
        result.append({
            'id': slot.id, 'slot_number': slot.slot_number, 'slot_type': slot.slot_type, 'status': slot.status,
            'vehicle': {
                'plate': vehicle.plate_number, 'owner': vehicle.owner_name,
                'time_in': vehicle.time_in.strftime('%I:%M %p')
            } if vehicle else None
        })
    return jsonify(result)


@app.route('/api/stats', methods=['GET'])
def get_stats():
    total = ParkingSlot.query.count()
    occupied = ParkingSlot.query.filter_by(status='Occupied').count()
    available = total - occupied
    vehicles_today = Vehicle.query.filter(
        Vehicle.time_in >= datetime.utcnow().replace(hour=0, minute=0, second=0)).count()
    return jsonify({
        'total': total, 'occupied': occupied, 'available': available,
        'vehicles_today': vehicles_today,
        'occupancy_rate': round((occupied / total) * 100, 1) if total > 0 else 0
    })


@app.route('/api/entry', methods=['POST'])
def vehicle_entry():
    data = request.json
    vehicle_type = data.get('vehicle_type')
    plate_number = data.get('plate_number', '').upper().strip()
    owner_name = data.get('owner_name', '').strip()

    if not plate_number or not owner_name: return jsonify({'error': 'Missing plate number or owner name'}), 400

    existing = Vehicle.query.filter_by(plate_number=plate_number, time_out=None).first()
    if existing: return jsonify({'error': f'Vehicle {plate_number} is already parked!'}), 400

    slot = ParkingSlot.query.filter_by(status='Available', slot_type=vehicle_type).first()
    if not slot: return jsonify({'error': f'No available slots for {vehicle_type}!'}), 400

    vehicle = Vehicle(plate_number=plate_number, owner_name=owner_name, vehicle_type=vehicle_type, slot_id=slot.id)
    db.session.add(vehicle)
    slot.status = 'Occupied';
    slot.vehicle_id = vehicle.id
    db.session.commit()

    log_to_csv({'Timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), 'Event': 'DEPLOYMENT',
                'Plate Number': vehicle.plate_number, 'Owner': vehicle.owner_name, 'Vehicle Type': vehicle.vehicle_type,
                'Slot': slot.slot_number, 'Fee': '', 'Duration': ''})

    return jsonify({'message': f'Vehicle parked successfully at Slot {slot.slot_number}', 'plate': vehicle.plate_number,
                    'slot': slot.slot_number}), 201


@app.route('/api/exit/<plate_number>', methods=['POST'])
def vehicle_exit(plate_number):
    plate_number = plate_number.upper().strip()
    vehicle = Vehicle.query.filter_by(plate_number=plate_number, time_out=None).first()
    if not vehicle: return jsonify({'error': f'Vehicle {plate_number} not found or already exited!'}), 404

    slot = ParkingSlot.query.get(vehicle.slot_id)
    time_diff = datetime.utcnow() - vehicle.time_in
    hours = math.ceil(time_diff.total_seconds() / 3600)
    fee = hours * 2.0

    total_seconds = int(time_diff.total_seconds())
    hrs, mins, secs = total_seconds // 3600, (total_seconds % 3600) // 60, total_seconds % 60
    duration_str = f"{hrs}h {mins}m {secs}s"

    vehicle.time_out = datetime.utcnow();
    vehicle.fee = fee
    slot.status = 'Available';
    slot.vehicle_id = None
    db.session.commit()

    log_to_csv({'Timestamp': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), 'Event': 'CLEARANCE',
                'Plate Number': vehicle.plate_number, 'Owner': vehicle.owner_name, 'Vehicle Type': vehicle.vehicle_type,
                'Slot': slot.slot_number, 'Fee': f'${fee:.2f}', 'Duration': duration_str})

    return jsonify({'message': f'Exit Success. Fee: ${fee:.2f}', 'fee': fee, 'slot': slot.slot_number,
                    'duration': duration_str}), 200


@app.route('/api/report/daily_pdf', methods=['GET'])
def daily_pdf_report():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    exited_vehicles = Vehicle.query.filter(Vehicle.time_out >= today_start).all()

    total_revenue = sum(v.fee for v in exited_vehicles)
    total_exits = len(exited_vehicles)

    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Helvetica", 'B', 16)
    pdf.cell(200, 10, txt="SmartPark Daily Clearance Report", ln=True, align='C')
    pdf.set_font("Helvetica", '', 12)
    pdf.cell(200, 10, txt=f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}", ln=True, align='C')
    pdf.ln(10)

    pdf.set_font("Helvetica", 'B', 12)
    pdf.cell(200, 10, txt="Daily Summary", ln=True)
    pdf.set_font("Helvetica", '', 12)
    pdf.cell(90, 10, txt="Total Vehicles Cleared:", border=1)
    pdf.cell(90, 10, txt=str(total_exits), border=1, ln=True)
    pdf.cell(90, 10, txt="Total Revenue Collected:", border=1)
    pdf.cell(90, 10, txt=f"${total_revenue:.2f}", border=1, ln=True)
    pdf.ln(10)

    pdf.set_font("Helvetica", 'B', 10)
    pdf.cell(30, 10, "Slot", border=1)
    pdf.cell(30, 10, "Plate", border=1)
    pdf.cell(40, 10, "Owner", border=1)
    pdf.cell(30, 10, "Type", border=1)
    pdf.cell(30, 10, "Duration", border=1)
    pdf.cell(30, 10, "Fee", border=1, ln=True)

    pdf.set_font("Helvetica", '', 9)
    for v in exited_vehicles:
        slot = ParkingSlot.query.get(v.slot_id)
        time_diff = v.time_out - v.time_in
        total_seconds = int(time_diff.total_seconds())
        hrs, mins, secs = total_seconds // 3600, (total_seconds % 3600) // 60, total_seconds % 60
        duration_str = f"{hrs}h {mins}m {secs}s"

        pdf.cell(30, 10, slot.slot_number if slot else "N/A", border=1)
        pdf.cell(30, 10, v.plate_number, border=1)
        pdf.cell(40, 10, v.owner_name, border=1)
        pdf.cell(30, 10, v.vehicle_type, border=1)
        pdf.cell(30, 10, duration_str, border=1)
        pdf.cell(30, 10, f"${v.fee:.2f}", border=1, ln=True)

    pdf_bytes = pdf.output()

    # FIX: as_attachment=False forces the browser to view it inline instead of downloading
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f'smartpark_report_{datetime.utcnow().strftime("%Y%m%d")}.pdf'
    )


@app.route('/api/report/daily_csv', methods=['GET'])
def daily_csv_report():
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    exited_vehicles = Vehicle.query.filter(Vehicle.time_out >= today_start).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        ['Slot', 'Plate Number', 'Owner Name', 'Vehicle Type', 'Time In', 'Time Out', 'Duration', 'Fee ($)'])

    total_revenue = 0.0
    for v in exited_vehicles:
        slot = ParkingSlot.query.get(v.slot_id)
        time_diff = v.time_out - v.time_in
        total_seconds = int(time_diff.total_seconds())
        hrs, mins, secs = total_seconds // 3600, (total_seconds % 3600) // 60, total_seconds % 60
        duration_str = f"{hrs}h {mins}m {secs}s"

        writer.writerow([
            slot.slot_number if slot else "N/A",
            v.plate_number,
            v.owner_name,
            v.vehicle_type,
            v.time_in.strftime('%Y-%m-%d %H:%M:%S'),
            v.time_out.strftime('%Y-%m-%d %H:%M:%S'),
            duration_str,
            f"{v.fee:.2f}"
        ])
        total_revenue += v.fee

    writer.writerow([])
    writer.writerow(['', '', '', '', '', 'TOTAL REVENUE:', '', f'{total_revenue:.2f}'])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'smartpark_report_{datetime.utcnow().strftime("%Y%m%d")}.csv'
    )


if __name__ == '__main__':
    app.run(debug=True)