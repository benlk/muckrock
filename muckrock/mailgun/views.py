"""
Views for mailgun
"""

from django.contrib.localflavor.us.us_states import STATE_CHOICES
from django.core.mail import EmailMessage, send_mail
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt

import hashlib
import hmac
import json
import logging
import os
import sys
import time
from datetime import datetime, date
from email.utils import parseaddr, getaddresses

from muckrock.agency.models import Agency
from muckrock.foia.models import FOIARequest, FOIACommunication, FOIAFile
from muckrock.foia.tasks import upload_document_cloud
from muckrock.settings import MAILGUN_ACCESS_KEY

logger = logging.getLogger(__name__)

# pylint: disable=bad-continuation
# pylint: disable=broad-except

def _make_orphan_comm(from_, to_, post, files, foia):
    """Make an orphan commuication"""
    from_realname, _ = parseaddr(from_)
    to_ = to_[:255] if to_ else to_
    comm = FOIACommunication.objects.create(
            priv_from_who=from_[:255], from_who=from_realname[:255],
            priv_to_who=to_, response=True,
            date=datetime.now(), full_html=False, delivered='email',
            communication='%s\n%s' %
                (post.get('stripped-text', ''), post.get('stripped-signature')),
            likely_foia=foia)
    RawEmail.objects.create(
        communication=comm,
        raw_email='%s\n%s' % (post.get('message-headers', ''), post.get('body-plain', '')))
    # handle attachments
    for file_ in files.itervalues():
        type_ = _file_type(file_)
        if type_ == 'file':
            _upload_file(None, comm, file_, from_)
    return comm

@csrf_exempt
def handle_request(request, mail_id):
    """Handle incoming mailgun FOI request messages"""

    post = request.POST
    if not _verify(post):
        return HttpResponseForbidden()
    from_ = post.get('From')
    to_ = post.get('To') or post.get('to')

    try:
        from_realname, from_email = parseaddr(from_)
        foia = FOIARequest.objects.get(mail_id=mail_id)

        if not _allowed_email(from_email, foia):
            msg = 'Bad Sender'
        if foia.block_incoming:
            msg = 'Incoming Blocked'
        if not _allowed_email(from_email, foia) or foia.block_incoming:
            logger.warning('%s: %s', msg, from_)
            comm = _make_orphan_comm(from_, to_, post, request.FILES, foia)
            extra_content = ['https://www.muckrock.com' + reverse('foia-orphans')]
            extra_content.append(
                'Probable request: https://www.muckrock.com' +
                reverse('admin:foia_foiarequest_change', args=(foia.pk,)))
            extra_content.append(
                'Move this comm to that request: https://www.muckrock.com' +
                reverse('foia-orphans') + ('?comm_id=%s' % comm.pk))
            _forward(post, request.FILES, msg, extra_content=extra_content)
            return HttpResponse('WARNING')

        comm = FOIACommunication.objects.create(
                foia=foia, from_who=from_realname[:255],
                to_who=foia.user.get_full_name(), response=True,
                date=datetime.now(), full_html=False, delivered='email',
                communication='%s\n%s' %
                    (post.get('stripped-text', ''), post.get('stripped-signature')))
        RawEmail.objects.create(
            communication=comm,
            raw_email='%s\n%s' % (post.get('message-headers', ''), post.get('body-plain', '')))

        # handle attachments
        for file_ in request.FILES.itervalues():
            type_ = _file_type(file_)
            if type_ == 'file':
                _upload_file(foia, comm, file_, from_)

        _forward(post, request.FILES)
        send_mail('[RESPONSE] Freedom of Information Request: %s' % foia.title,
                  render_to_string('text/foia/admin_request.txt',
                                   {'request': foia, 'post': post,
                                    'date': date.today().toordinal()}),
                  'info@muckrock.com', ['requests@muckrock.com'], fail_silently=False)

        foia.email = from_email
        foia.other_emails = ','.join(email for name, email
                                     in getaddresses([post.get('To', ''), post.get('Cc', '')])
                                     if email and not email.endswith('muckrock.com'))
        while len(foia.other_emails) > 255:
            # drop emails until it fits in db
            foia.other_emails = foia.other_emails[:foia.other_emails.rindex(',')]

        if foia.status == 'ack':
            foia.status = 'processed'
        foia.save()
        foia.update(comm.anchor())

        # NLTK

    except FOIARequest.DoesNotExist:
        logger.warning('Invalid Address: %s', mail_id)
        foia = None
        try:
            # try to get the foia by the PK before the dash
            foia = FOIARequest.objects.get(pk=mail_id.split('-')[0])
        except FOIARequest.DoesNotExist:
            pass
        comm = _make_orphan_comm(from_, to_, post, request.FILES, foia)
        extra_content = ['https://www.muckrock.com' + reverse('foia-orphans')]
        extra_content.append('Target address: %s@requests.muckrock.com' % mail_id)
        if foia:
            extra_content.append(
                'Probable request: https://www.muckrock.com' +
                reverse('admin:foia_foiarequest_change', args=(foia.pk,)))
            extra_content.append(
                'Move this comm to that request: https://www.muckrock.com' +
                reverse('foia-orphans') + ('?comm_id=%s' % comm.pk))
        _forward(post, request.FILES, 'Invalid Address',
                extra_content='\n'.join(extra_content))
        return HttpResponse('WARNING')
    except Exception:
        # If anything I haven't accounted for happens, at the very least forward
        # the email to requests so it isn't lost
        logger.error('Uncaught Mailgun Exception: %s', mail_id, exc_info=sys.exc_info())
        _forward(post, request.FILES, 'Uncaught Mailgun Exception')
        return HttpResponse('ERROR')

    return HttpResponse('OK')

@csrf_exempt
def fax(request):
    """Handle fax confirmations"""

    if not _verify(request.POST):
        return HttpResponseForbidden()

    _forward(request.POST, request.FILES)
    return HttpResponse('OK')

@csrf_exempt
def bounces(request):
    """Notify when an email is bounced"""

    if not _verify(request.POST):
        return HttpResponseForbidden()

    recipient = request.POST.get('recipient', 'none@example.com')
    agencies = Agency.objects.filter(Q(email__iexact=recipient) |
                                     Q(other_emails__icontains=recipient))
    foias = FOIARequest.objects.filter(Q(email__iexact=recipient) |
                                       Q(other_emails__icontains=recipient))\
                               .filter(status__in=['ack', 'processed', 'appealing', 'fix',
                                                   'payment'])

    event = request.POST.get('event')
    if event == 'bounced':
        error = request.POST.get('error')
    elif event == 'dropped':
        error = request.POST.get('description')

    try:
        headers = request.POST['message-headers']
        parsed_headers = json.loads(headers)
        from_header = [v for k, v in parsed_headers if k == 'From'][0]
        _, from_email = parseaddr(from_header)
        foia_id = from_email[:from_email.index('-')]
        foia = FOIARequest.objects.get(pk=foia_id)
    except (IndexError, ValueError, KeyError):
        foia = None

    send_mail('[%s] %s' % (event.upper(), recipient),
              render_to_string('text/foia/bounce.txt',
                               {'agencies': agencies, 'recipient': recipient,
                                'foia': foia, 'foias': foias, 'error': error}),
              'info@muckrock.com', ['requests@muckrock.com'], fail_silently=False)

    return HttpResponse('OK')

@csrf_exempt
def opened(request):
    """Notify when an email has been opened"""

    if not _verify(request.POST):
        return HttpResponseForbidden()

    comm_id = request.POST.get('comm_id')
    if comm_id:
        try:
            comm = FOIACommunication.objects.get(pk=comm_id)
            comm.opened = True
            comm.save()
        except FOIACommunication.DoesNotExist:
            logger.warning('Trying to mark missing communication as opened: %s', comm_id)

    return HttpResponse('OK')

def _verify(post):
    """Verify that the message is from mailgun"""
    token = post.get('token')
    timestamp = post.get('timestamp')
    signature = post.get('signature')
    return (signature == hmac.new(key=MAILGUN_ACCESS_KEY,
                                  msg='%s%s' % (timestamp, token),
                                  digestmod=hashlib.sha256).hexdigest()) \
           and int(timestamp) + 300 > time.time()

def _forward(post, files, title='', extra_content=''):
    """Forward an email from mailgun to admin"""
    if title:
        subject = '%s: %s' % (title, post.get('subject', ''))
    else:
        subject = post.get('subject', '')
    subject = subject.replace('\r', '').replace('\n', '')

    if extra_content:
        body = '%s\n\n%s' % (extra_content, post.get('body-plain'))
    else:
        body = post.get('body-plain')

    email = EmailMessage(subject, body, post.get('From'), ['requests@muckrock.com'])
    for file_ in files.itervalues():
        email.attach(file_.name, file_.read(), file_.content_type)

    email.send(fail_silently=False)

def _upload_file(foia, comm, file_, sender):
    """Upload a file to attach to a FOIA request"""
    # pylint: disable=E1101

    access = 'private' if foia and foia.is_embargo() else 'public'
    source = foia.agency.name if foia and foia.agency else sender

    foia_file = FOIAFile(foia=foia, comm=comm, title=os.path.splitext(file_.name)[0][:70],
                         date=datetime.now(), source=source[:70], access=access)
    foia_file.ffile.save(file_.name[:100].encode('ascii', 'ignore'), file_)
    foia_file.save()
    if foia:
        upload_document_cloud.apply_async(args=[foia_file.pk, False], countdown=3)

def _allowed_email(email, foia):
    """Is this an allowed email?"""

    email = email.lower()
    state_tlds = ['.%s.us' % a.lower() for (a, _) in STATE_CHOICES
                                      if a not in ('AS', 'DC', 'GU', 'MP', 'PR', 'VI')]
    allowed_tlds = [
        '.gov',
        '.mil',
        '.muckrock.com',
        '@muckrock.com',
        ] + state_tlds
    if foia.email and '@' in foia.email and email.endswith(foia.email.split('@')[1].lower()):
        return True
    if foia.agency and email in [i.lower() for i in foia.agency.get_other_emails()]:
        return True
    if email in [i.lower() for i in foia.get_other_emails()]:
        return True
    return any(email.endswith(tld) for tld in allowed_tlds)

def _file_type(file_):
    """Determine the attachment's file type"""

    ignore_types = [('application/x-pkcs7-signature', 'p7s')]

    if any(file_.content_type == itt or file_.name.endswith(ite) for itt, ite in ignore_types):
        return 'ignore'
    else:
        return 'file'

