from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .forms import CustomerRegisterForm
from .models import Profile
from .forms import ProducerRegisterForm

def producer_register_view(request):
    if request.method == "POST":
        form = ProducerRegisterForm(request.POST)
        if form.is_valid():
            form.save()  # Form will handle user + profile creation

            messages.success(request, "Producer account created. Please log in.")
            return redirect("accounts:login")
    else:
        form = ProducerRegisterForm()

    return render(request, "accounts/producer_register.html", {"form": form})

def register_view(request):
    if request.method == "POST":
        form = CustomerRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, "Account created. Please log in.")
            return redirect("accounts:login")
    else:
        form = CustomerRegisterForm()

    return render(request, "accounts/register.html", {"form": form})


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=username, password=password)

        if user is None:
            messages.error(request, "Invalid username or password.")
            return render(request, "accounts/login.html")

        login(request, user)

        # Ensure profile exists (handles admin/superuser too)
        profile, _ = Profile.objects.get_or_create(user=user)

        # Role-based redirect
        if profile.role == Profile.Role.PRODUCER:
            return redirect("marketplace:producer_product_list")

        return redirect("marketplace:product_list")

    return render(request, "accounts/login.html")

@login_required
def profile_view(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    return render(request, "accounts/profile.html", {"profile": profile})


 #CART 
def logout_view(request):
    # preserve the shopping cart through the logout process
    cart = request.session.get("cart")
    logout(request)  # this flushes the session
    if cart is not None:
        # new session created by logout, restore cart
        request.session["cart"] = cart
    return redirect("accounts:login")
