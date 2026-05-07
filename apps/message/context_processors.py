from .models import MessageThread

def unread_messages(request):
    if request.user.is_authenticated:
        unread_count = MessageThread.objects.filter(
            participants=request.user
        ).count()
        # Check if any thread is unread
        has_unread = any(
            thread.is_unread_for(request.user)
            for thread in MessageThread.objects.filter(participants=request.user)
        )
        return {"has_unread_messages": has_unread}
    return {"has_unread_messages": False}