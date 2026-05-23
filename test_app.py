import pytest
import sys
import os

# This tells Python to look in the current folder for the app.py file
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import app, db, ParkingSlot, Vehicle, seed_database


@pytest.fixture
def client():
    """Configures the Flask app for testing with an in-memory SQLite database."""
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'  # Use RAM for testing

    with app.app_context():
        db.create_all()
        seed_database()  # Populate the 40 slots
        yield app.test_client()
        db.drop_all()  # Clean up after tests


def test_index_route(client):
    """Test that the main UI route loads successfully."""
    response = client.get('/')
    assert response.status_code == 200
    assert b'SMARTPARK' in response.data


def test_get_stats_empty(client):
    """Test stats endpoint when no vehicles are parked."""
    response = client.get('/api/stats')
    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] == 40
    assert data['occupied'] == 0
    assert data['available'] == 40
    assert data['occupancy_rate'] == 0.0


def test_get_slots(client):
    """Test that all 40 slots are returned."""
    response = client.get('/api/slots')
    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 40


def test_vehicle_entry_success(client):
    """Test successful vehicle deployment."""
    payload = {'plate_number': 'ABC-1234', 'owner_name': 'John Doe', 'vehicle_type': 'Car'}
    response = client.post('/api/entry', json=payload)
    assert response.status_code == 201
    data = response.get_json()
    assert 'parked successfully' in data['message']
    assert data['plate'] == 'ABC-1234'
    assert data['slot'].startswith('C-')  # Car slots start with C-


def test_vehicle_entry_missing_data(client):
    """Test entry with missing plate number."""
    payload = {'plate_number': '', 'owner_name': 'Jane Doe', 'vehicle_type': 'Car'}
    response = client.post('/api/entry', json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert 'Missing plate number' in data['error']


def test_vehicle_entry_duplicate(client):
    """Test entering a vehicle that is already parked."""
    payload = {'plate_number': 'DUP-1111', 'owner_name': 'Alice', 'vehicle_type': 'Car'}
    client.post('/api/entry', json=payload)  # First entry
    response = client.post('/api/entry', json=payload)  # Duplicate attempt
    assert response.status_code == 400
    data = response.get_json()
    assert 'already parked' in data['error']


def test_vehicle_entry_full_zone(client):
    """Test entering a vehicle when its specific zone is full."""
    # Fill up all 10 Motorcycle slots
    for i in range(1, 11):
        payload = {'plate_number': f'MOTO-{i}', 'owner_name': 'Rider', 'vehicle_type': 'Motorcycle'}
        client.post('/api/entry', json=payload)

    # Attempt to park the 11th motorcycle
    payload = {'plate_number': 'MOTO-11', 'owner_name': 'Rider', 'vehicle_type': 'Motorcycle'}
    response = client.post('/api/entry', json=payload)
    assert response.status_code == 400
    data = response.get_json()
    assert 'No available slots' in data['error']


def test_vehicle_exit_success(client):
    """Test successful vehicle exit and fee calculation."""
    # Park a vehicle first
    payload = {'plate_number': 'EXIT-999', 'owner_name': 'Bob', 'vehicle_type': 'Van'}
    client.post('/api/entry', json=payload)

    # Process exit
    response = client.post('/api/exit/EXIT-999')
    assert response.status_code == 200
    data = response.get_json()
    assert 'Exit Success' in data['message']
    assert data['fee'] == 2.0  # Because math.ceil of any time < 1 hour is 1 * $2.00
    assert data['slot'].startswith('V-')  # Van slot


def test_vehicle_exit_not_found(client):
    """Test exiting a vehicle that doesn't exist."""
    response = client.post('/api/exit/NOCAR-00')
    assert response.status_code == 404
    data = response.get_json()
    assert 'not found' in data['error']


def test_report_pdf(client):
    """Test PDF report generation."""
    response = client.get('/api/report/daily_pdf')
    assert response.status_code == 200
    assert response.content_type == 'application/pdf'


def test_report_csv(client):
    """Test CSV report generation."""
    response = client.get('/api/report/daily_csv')
    assert response.status_code == 200
    assert response.content_type == 'text/csv; charset=utf-8'