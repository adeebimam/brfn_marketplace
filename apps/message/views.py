from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404

from .models import MessageThread, Message
from .forms import StartMessageForm, ReplyForm
from .services import get_allowed_message_recipients


@login_required
def inbox(request):
    threads = MessageThread.objects.filter(
        participants=request.user
    ).order_by("-updated_at")

    for thread in threads:
        thread.unread_for_user = thread.is_unread_for(request.user)

    return render(request, "message/inbox.html", {
        "threads": threads
    })


@login_required
def start_message(request):
    allowed_recipients = get_allowed_message_recipients(request.user)

    if request.method == "POST":
        form = StartMessageForm(
            request.POST,
            allowed_recipients=allowed_recipients
        )

        if form.is_valid():
            recipient = form.cleaned_data["recipient"]
            subject = form.cleaned_data["subject"]
            body = form.cleaned_data["body"]

            if recipient not in allowed_recipients:
                return HttpResponseForbidden("You cannot message this user.")

            thread = MessageThread.objects.create(
                subject=subject,
                created_by=request.user
            )

            thread.participants.add(request.user, recipient)

            first_message = Message.objects.create(
                thread=thread,
                sender=request.user,
                body=body
            )

            first_message.read_by.add(request.user)

            return redirect("message:thread_detail", thread_id=thread.id)

    else:
        form = StartMessageForm(
            allowed_recipients=allowed_recipients
        )

    return render(request, "message/message_form.html", {
        "form": form
    })


@login_required
def thread_detail(request, thread_id):
    thread = get_object_or_404(MessageThread, id=thread_id)

    if not thread.participants.filter(id=request.user.id).exists():
        return HttpResponseForbidden("You cannot view this conversation.")

    messages = thread.messages.select_related("sender")

    for message in messages.exclude(sender=request.user):
        message.read_by.add(request.user)

    if request.method == "POST":
        form = ReplyForm(request.POST)

        if form.is_valid():
            new_message = Message.objects.create(
                thread=thread,
                sender=request.user,
                body=form.cleaned_data["body"]
            )

            new_message.read_by.add(request.user)

            thread.save()

            return redirect("message:thread_detail", thread_id=thread.id)

    else:
        form = ReplyForm()

    return render(request, "message/thread_detail.html", {
        "thread": thread,
        "messages": messages,
        "form": form
    })