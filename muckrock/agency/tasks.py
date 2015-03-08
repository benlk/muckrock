"""Celery Tasks for the agency application"""

from celery.schedules import crontab
from celery.task import periodic_task

from muckrock.agency.models import Agency
from muckrock.task.models import StaleAgencyTask

@periodic_task(run_every=crontab(day_of_week='sunday', hour=4, minute=0),
               name='muckrock.agency.tasks.stale')
def stale():
    """Record all stale agencies once a week"""
    agencies = Agency.objects.all()

    for agency in agencies:
        latest_response = agency.latest_response()
        if latest_response >= 30 or agency.expired():
            agency.stale = True
            agency.save()
            StaleAgencyTask.objects.create(agency=agency)
