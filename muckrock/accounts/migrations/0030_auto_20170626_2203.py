# -*- coding: utf-8 -*-
# Generated by Django 1.9.9 on 2017-06-26 22:03
from __future__ import unicode_literals

import django.core.files.storage
from django.db import migrations, models
import easy_thumbnails.fields


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0029_auto_20170605_2134'),
    ]

    operations = [
        migrations.AddField(
            model_name='statistics',
            name='machine_requests_lawsuit',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='statistics',
            name='total_requests_lawsuit',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
