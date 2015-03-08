"""
Models for the Task application
"""
from django.contrib import messages
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.db.models.loading import get_model

from datetime import datetime

from muckrock.agency.models import Agency
from muckrock.jurisdiction.models import Jurisdiction


class Task(models.Model):
    """A base task model for fields common to all tasks"""

    date_created = models.DateTimeField(auto_now_add=True)
    date_done = models.DateTimeField(blank=True, null=True)
    resolved = models.BooleanField(default=False)
    assigned = models.ForeignKey(User, blank=True, null=True)

    class Meta:
        ordering = ['date_created']

    def __unicode__(self):
        return 'Task: %d' % (self.pk)

    def handle_post(self, request):
        """Handle form actions for this task"""
        if request.POST.get('submit') == 'Resolve':
            self.resolve()
            messages.success(request, 'Resolved the task')

    def resolve(self):
        """Resolve the task"""
        self.resolved = True
        self.date_done = datetime.now()
        self.save()


class OrphanTask(Task):
    """A communication that needs to be approved before showing it on the site"""

    reasons = (('bs', 'Bad Sender'),
               ('ib', 'Incoming Blocked'),
               ('ia', 'Invalid Address'))
    reason = models.CharField(max_length=2, choices=reasons)
    communication = models.ForeignKey('foia.FOIACommunication')
    address = models.CharField(max_length=255)

    def __unicode__(self):
        # pylint: disable=no-member
        return '%s: %s' % (self.get_reason_display(), self.communication.foia)

    def handle_post(self, request):
        """Handle form actions for this task"""
        # pylint: disable=no-member
        comm = self.communication
        submit = request.POST.get('submit')
        if submit == 'Move':
            foia_pks = request.POST.get('foia_pk', '').split(',')
            comm.move(request, foia_pks)

        if submit == 'Reject':
            messages.success(request, 'Rejected the communication')

        if submit in ('Move', 'Reject'):
            self.resolve()


class SnailMailTask(Task):
    """A communication that needs to be snail mailed"""

    categories = (('a', 'Appeal'), ('n', 'New'), ('u', 'Update'))
    category = models.CharField(max_length=1, choices=categories)
    communication = models.ForeignKey('foia.FOIACommunication')

    def __unicode__(self):
        # pylint: disable=no-member
        return '%s: %s' % (self.get_category_display(), self.communication.foia)

    def handle_post(self, request):
        """Handle form actions for this task"""
        # pylint: disable=no-member
        comm = self.communication
        foia = comm.foia
        statuses = {
                'Awaiting Acknowledgement': 'ack',
                'Awaiting Response': 'processed',
                'Awaiting Appeal': 'appealing',
                }
        if request.POST.get('submit') in statuses:
            foia.status = statuses[request.POST.get('submit')]
            foia.update()
            foia.save()
            comm.status = foia.status
            comm.date = datetime.now()
            comm.save()
            self.resolve()
            messages.success(request, 'Set the status for the mailed request')


class RejectedEmailTask(Task):
    """A FOIA request has had an outgoing email rejected"""

    categories = (('b', 'Bounced'), ('d', 'Dropped'))
    category = models.CharField(max_length=1, choices=categories)
    foia = models.ForeignKey('foia.FOIARequest', blank=True, null=True)
    email = models.EmailField(blank=True)
    error = models.CharField(max_length=255, blank=True)

    def __unicode__(self):
        return '%s: %s' % (self.get_category_display(), self.foia)

    def agencies(self):
        """Get the agencies who use this email address"""
        return Agency.objects.filter(Q(email__iexact=self.email) |
                                     Q(other_emails__icontains=self.email))

    def foias(self):
        """Get the FOIAs who use this email address"""
        # to avoid circular dependencies
        # pylint: disable=invalid-name
        FOIARequest = get_model('foia', 'FOIARequest')
        return FOIARequest.objects\
                .filter(Q(email__iexact=self.email) |
                        Q(other_emails__icontains=self.email))\
                .filter(status__in=['ack', 'processed', 'appealing',
                                    'fix', 'payment'])


class StaleAgencyTask(Task):
    """An agency has gone stale"""

    agency = models.ForeignKey(Agency)

    def __unicode__(self):
        return 'Stale Agency: %s' % (self.agency)


class FlaggedTask(Task):
    """A user has flagged a request, agency or jurisdiction"""

    user = models.ForeignKey(User)
    text = models.TextField()

    foia = models.ForeignKey('foia.FOIARequest', blank=True, null=True)
    agency = models.ForeignKey(Agency, blank=True, null=True)
    jurisdiction = models.ForeignKey(Jurisdiction, blank=True, null=True)

    def __unicode__(self):
        if self.foia:
            return 'Flagged: %s' % (self.foia)
        if self.agency:
            return 'Flagged: %s' % (self.agency)
        if self.jurisdiction:
            return 'Flagged: %s' % (self.jurisdiction)
        return 'Flagged: <None>'


class NewAgencyTask(Task):
    """A new agency has been created and needs approval"""

    user = models.ForeignKey(User, blank=True, null=True)
    agency = models.ForeignKey(Agency)

    def __unicode__(self):
        return 'New Agency: %s' % (self.agency)

    def handle_post(self, request):
        """Handle form actions for this task"""
        submit = request.POST.get('submit')

        if submit == 'Approve':
            agency = self.agency
            agency.approved = True
            agency.save()
            messages.success(request, 'Approved the agency')

        if submit == 'Reject':
            messages.error(request, 'Rejected the agency')

        if submit in ('Approve', 'Reject'):
            self.resolve()


class ResponseTask(Task):
    """A response has been received and needs its status set"""

    communication = models.ForeignKey('foia.FOIACommunication')

    def __unicode__(self):
        # pylint: disable=no-member
        return 'Response: %s' % (self.communication.foia)

    def handle_post(self, request):
        """Handle form actions for this task"""
        # pylint: disable=no-member
        comm = self.communication
        foia = comm.foia
        statuses = ['fix', 'paymnet', 'rejected', 'no_docs',
                    'done', 'partial', 'abandoned']
        status = request.POST.get('status')
        if status in statuses:
            foia.status = status
            foia.update()
            if status in ['rejected', 'no_docs', 'done', 'abandoned']:
                foia.date_done = comm.date
            foia.save()
            comm.status = foia.status
            if status in ['ack', 'processed', 'appealing']:
                comm.date = datetime.now()
            comm.save()
            self.resolve()
            messages.success(request, 'Set the status')
