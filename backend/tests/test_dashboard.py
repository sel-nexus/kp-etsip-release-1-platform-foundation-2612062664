"""Exercise executive metrics and authorization against disposable MongoDB."""

from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def dashboard():
    """Start the real application with a unique seeded database."""
    name = 'ketsip_dashboard_test_' + uuid4().hex
    settings = Settings(environment='test', jwt_secret='test-signing-key-01234567890123456789', mongodb_uri='mongodb://127.0.0.1:27017', mongodb_db=name, seed_on_startup=True, seed_admin_password='Initial-Admin-Password123!', seed_demo_password='Initial-Demo-Password123!')
    mongo = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    try:
        with TestClient(create_app(settings)) as http:
            db = mongo[name]
            db.applications.delete_many({})
            portfolios = list(db.portfolios.find().sort('name', 1))
            db.applications.insert_many([
                {'name': 'A', 'portfolioId': str(portfolios[0]['_id']), 'productArea': 'Alpha', 'qualityScore': 70, 'applicationHealthScore': 80, 'automationCoverage': 20, 'openCriticalDefects': 2, 'riskLevel': 'High'},
                {'name': 'B', 'portfolioId': str(portfolios[0]['_id']), 'productArea': 'Beta', 'qualityScore': 71, 'applicationHealthScore': 81, 'automationCoverage': 21, 'openCriticalDefects': 3, 'riskLevel': 'Low'},
                {'name': 'C', 'portfolioId': str(portfolios[1]['_id']), 'productArea': 'Alpha', 'qualityScore': 99, 'applicationHealthScore': 90, 'automationCoverage': 50, 'openCriticalDefects': 4, 'riskLevel': 'High'},
            ])
            yield http, db, portfolios
    finally:
        mongo.drop_database(name)
        mongo.close()


def login(http, role='executive'):
    """Obtain a seeded user's bearer token."""
    response = http.post('/api/auth/login', json={'email': f'{role}@ketsip.local', 'password': 'Initial-Demo-Password123!'})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def test_formulas_empty_portfolio_and_paginated_breakdown(dashboard):
    """Use all applications for totals even when the breakdown is paginated."""
    http, _, portfolios = dashboard
    headers = login(http)
    data = http.get('/api/dashboard/executive?limit=1', headers=headers).json()
    assert {key: data[key] for key in ('enterpriseQualityScore', 'applicationHealthScore', 'automationMaturityScore', 'openCriticalDefects', 'applicationsAtRisk', 'totalApplications', 'totalPortfolios')} == {
        'enterpriseQualityScore': 80, 'applicationHealthScore': 84, 'automationMaturityScore': 30,
        'openCriticalDefects': 9, 'applicationsAtRisk': 2, 'totalApplications': 3, 'totalPortfolios': 3}
    assert data['portfolios']['total'] == 3 and len(data['portfolios']['items']) == 1
    first = data['portfolios']['items'][0]
    assert first['name'] == portfolios[0]['name'] and first['qualityScore'] == 71 and first['applicationsAtRisk'] == 1
    last = http.get('/api/dashboard/executive?limit=1&offset=2', headers=headers).json()['portfolios']['items'][0]
    assert last['qualityScore'] is None and last['applicationCount'] == 0 and last['applicationsAtRisk'] == 0


def test_each_filter_combination_and_no_matches(dashboard):
    """Apply filters to applications and count only portfolios containing matches."""
    http, _, portfolios = dashboard
    headers = login(http)
    endpoint = '/api/dashboard/executive'
    by_portfolio = http.get(endpoint, params={'portfolioId': str(portfolios[0]['_id'])}, headers=headers).json()
    assert by_portfolio['totalPortfolios'] == 1 and by_portfolio['totalApplications'] == 2 and by_portfolio['enterpriseQualityScore'] == 71
    area = http.get(endpoint, params={'productArea': 'Alpha'}, headers=headers).json()
    assert area['totalPortfolios'] == 2 and area['totalApplications'] == 2 and area['enterpriseQualityScore'] == 85
    risk = http.get(endpoint, params={'riskLevel': 'Low'}, headers=headers).json()
    assert risk['totalPortfolios'] == 1 and risk['totalApplications'] == 1 and risk['applicationsAtRisk'] == 0
    combined = http.get(endpoint, params={'portfolioId': str(portfolios[0]['_id']), 'productArea': 'Alpha', 'riskLevel': 'High'}, headers=headers).json()
    assert combined['totalApplications'] == 1 and combined['openCriticalDefects'] == 2
    empty = http.get(endpoint, params={'productArea': 'missing'}, headers=headers).json()
    assert empty['totalPortfolios'] == empty['totalApplications'] == 0
    assert empty['enterpriseQualityScore'] is empty['applicationHealthScore'] is empty['automationMaturityScore'] is None
    assert empty['portfolios'] == {'items': [], 'total': 0}
    empty_portfolio = http.get(endpoint, params={'portfolioId': str(portfolios[2]['_id'])}, headers=headers).json()
    assert empty_portfolio['totalPortfolios'] == 1 and empty_portfolio['enterpriseQualityScore'] is None


def test_auth_scope_and_input_validation(dashboard):
    """Require a current view grant and never expose outside assignments."""
    http, db, portfolios = dashboard
    endpoint = '/api/dashboard/executive'
    assert http.get(endpoint).status_code == 401
    leader = login(http, 'leader')
    db.users.update_one({'email': 'leader@ketsip.local'}, {'$set': {'portfolioAccess': [str(portfolios[0]['_id'])]}})
    scoped = http.get(endpoint, headers=leader).json()
    assert scoped['totalPortfolios'] == 1 and scoped['totalApplications'] == 2
    outside = http.get(endpoint, params={'portfolioId': str(portfolios[1]['_id'])}, headers=leader).json()
    assert outside['totalPortfolios'] == 0 and outside['portfolios']['items'] == []
    db.users.update_one({'email': 'leader@ketsip.local'}, {'$set': {'portfolioAccess': []}})
    assert http.get(endpoint, headers=leader).json()['totalPortfolios'] == 0
    assert http.get(endpoint, params={'portfolioId': 'bad'}, headers=leader).status_code == 422
    assert http.get(endpoint, params={'riskLevel': 'Severe'}, headers=leader).status_code == 422
    assert http.get(endpoint, params={'limit': 101}, headers=leader).status_code == 422
    db.roles.update_one({'name': 'Portfolio Leader'}, {'$set': {'permissions': []}})
    assert http.get(endpoint, headers=leader).status_code == 403
