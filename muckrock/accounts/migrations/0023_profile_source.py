# -*- coding: utf-8 -*-
# Generated by Django 1.9.9 on 2017-03-12 22:12
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0022_receiptemail'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='source',
            field=models.CharField(blank=True, choices=[(b'foia machine', b'FOIA Machine')], max_length=20),
        ),
    ]
