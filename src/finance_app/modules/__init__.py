"""Application module registration."""

from typing import Any

from finance_app.modules.auth.controller import auth_bp
from finance_app.modules.calendar.controller import calendar_bp
from finance_app.modules.comparison.controller import comparison_bp
from finance_app.modules.dashboard.controller import dashboard_bp
from finance_app.modules.home.controller import home_bp
from finance_app.modules.jobs.controller import jobs_bp
from finance_app.modules.merchants.controller import merchants_bp
from finance_app.modules.prompt_lab.controller import prompt_lab_bp
from finance_app.modules.recurring.controller import recurring_bp
from finance_app.modules.reimbursements.controller import reimbursements_bp
from finance_app.modules.reports.controller import reports_bp
from finance_app.modules.review.controller import review_bp
from finance_app.modules.rules.controller import rules_bp
from finance_app.modules.settings.controller import settings_bp
from finance_app.modules.taxonomy_admin.controller import taxonomy_admin_bp
from finance_app.modules.transactions.controller import transactions_bp
from finance_app.modules.upload.controller import upload_bp


def register_blueprints(app: Any) -> None:
    """Register blueprints."""
    app.register_blueprint(auth_bp)
    app.register_blueprint(home_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(taxonomy_admin_bp)
    app.register_blueprint(prompt_lab_bp)
    app.register_blueprint(upload_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(comparison_bp)
    app.register_blueprint(merchants_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(recurring_bp)
    app.register_blueprint(reimbursements_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(rules_bp)
    app.register_blueprint(settings_bp)
