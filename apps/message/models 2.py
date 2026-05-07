from django.conf import settings
from django.db import models


class MessageThread(models.Model):
    subject = models.CharField(max_length=255)

    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="message_threads"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_message_threads"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    related_order = models.ForeignKey(
        "marketplace.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_threads"
    )

    related_producer_order = models.ForeignKey(
        "marketplace.ProducerOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_threads"
    )

    def is_unread_for(self, user):
        return self.messages.exclude(read_by=user).exclude(sender=user).exists()

    def __str__(self):
        return self.subject

    class Meta:
        ordering = ["-updated_at"]


class Message(models.Model):
    thread = models.ForeignKey(
        MessageThread,
        on_delete=models.CASCADE,
        related_name="messages"
    )

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_messages"
    )

    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    read_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="read_messages"
    )

    def __str__(self):
        return self.body[:50]

    class Meta:
        ordering = ["created_at"]