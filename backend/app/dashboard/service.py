"""Compute scoped executive dashboard aggregates in MongoDB."""

import math
from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from app.portfolios.repository import PortfolioRepository
from app.portfolios.service import authorize, scope


class DashboardRepository:
    """Read complete aggregate metrics and a bounded portfolio breakdown."""

    def __init__(self, db: AsyncDatabase) -> None:
        """Bind the shared asynchronous database."""
        self.db = db

    async def summary(self, visible: list[ObjectId] | None, portfolio_id: str | None, product_area: str, risk: str | None, limit: int, offset: int) -> dict:
        """Aggregate matching applications for each visible portfolio in MongoDB."""
        match: dict = {}
        if visible is not None:
            match['_id'] = {'$in': visible}
        if portfolio_id:
            selected = ObjectId(portfolio_id)
            match['_id'] = selected if visible is None or selected in visible else {'$in': []}
        app_match: dict = {'$expr': {'$eq': ['$portfolioId', '$$pid']}}
        if product_area:
            app_match['productArea'] = product_area
        if risk:
            app_match['riskLevel'] = risk
        pipeline = [
            {'$match': match},
            {'$lookup': {'from': 'applications', 'let': {'pid': {'$toString': '$_id'}}, 'pipeline': [
                {'$match': app_match},
                {'$group': {'_id': None, 'count': {'$sum': 1},
                    'qualitySum': {'$sum': '$qualityScore'}, 'qualityCount': {'$sum': {'$cond': [{'$isNumber': '$qualityScore'}, 1, 0]}},
                    'healthSum': {'$sum': '$applicationHealthScore'}, 'healthCount': {'$sum': {'$cond': [{'$isNumber': '$applicationHealthScore'}, 1, 0]}},
                    'automationSum': {'$sum': '$automationCoverage'}, 'automationCount': {'$sum': {'$cond': [{'$isNumber': '$automationCoverage'}, 1, 0]}},
                    'defects': {'$sum': '$openCriticalDefects'}, 'atRisk': {'$sum': {'$cond': [{'$eq': ['$riskLevel', 'High']}, 1, 0]}}}}
            ], 'as': 'stats'}},
            {'$set': {'stats': {'$arrayElemAt': ['$stats', 0]}}},
        ]
        if product_area or risk:
            pipeline.append({'$match': {'stats.count': {'$gt': 0}}})
        pipeline.extend([
            {'$sort': {'name': 1, '_id': 1}},
            {'$facet': {
                'items': [{'$skip': offset}, {'$limit': limit}, {'$project': {'name': 1, 'stats': 1}}],
                'totals': [{'$group': {'_id': None, 'portfolios': {'$sum': 1},
                    **{field: {'$sum': f'$stats.{field}'} for field in ('count', 'qualitySum', 'qualityCount', 'healthSum', 'healthCount', 'automationSum', 'automationCount', 'defects', 'atRisk')}}}]
            }}
        ])
        return (await (await self.db.portfolios.aggregate(pipeline)).to_list(1))[0]


def average(total: float, count: int) -> int | None:
    """Round non-negative scores to nearest integer or return no data."""
    return math.floor(total / count + 0.5) if count else None


async def executive(db: AsyncDatabase, user: dict, portfolio_id: str | None, product_area: str, risk: str | None, limit: int, offset: int) -> dict:
    """Authorize the caller and present the complete scoped metrics."""
    await authorize(PortfolioRepository(db), user, 'view')
    result = await DashboardRepository(db).summary(scope(user), portfolio_id, product_area, risk, limit, offset)
    totals = result['totals'][0] if result['totals'] else {}
    def score(prefix: str) -> int | None:
        """Calculate a score from its sum and sample count."""
        return average(totals.get(prefix + 'Sum', 0), totals.get(prefix + 'Count', 0))
    items = []
    for doc in result['items']:
        stats = doc.get('stats') or {}
        items.append({'id': str(doc['_id']), 'name': doc['name'], 'applicationCount': stats.get('count', 0),
                      'qualityScore': average(stats.get('qualitySum', 0), stats.get('qualityCount', 0)), 'applicationsAtRisk': stats.get('atRisk', 0)})
    return {'enterpriseQualityScore': score('quality'), 'applicationHealthScore': score('health'),
            'automationMaturityScore': score('automation'), 'openCriticalDefects': totals.get('defects', 0),
            'applicationsAtRisk': totals.get('atRisk', 0), 'totalApplications': totals.get('count', 0),
            'totalPortfolios': totals.get('portfolios', 0), 'portfolios': {'items': items, 'total': totals.get('portfolios', 0)}}
