from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Profile

class CustomerRegisterForm(UserCreationForm):
    ACCOUNT_TYPE_CHOICES = [
        (Profile.Role.CUSTOMER, "Customer"),
        (Profile.Role.COMMUNITY_GROUP, "Community Group"),
        (Profile.Role.RESTAURANT, "Restaurant / Café"),
    ]

    email = forms.EmailField(required=True)
    account_type = forms.ChoiceField(
        choices=ACCOUNT_TYPE_CHOICES,
        initial=Profile.Role.CUSTOMER,
        widget=forms.RadioSelect,
        label="Account type",
    )
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=True)
    business_name = forms.CharField(
        max_length=255,
        required=False,
        label="Organisation / Business name",
        help_text="Required for Community Groups and Restaurants / Cafés.",
    )
    business_type = forms.ChoiceField(
        choices=[("", "– Select type –")] + list(Profile.BusinessType.choices),
        required=False,
        label="Type of establishment",
    )
    delivery_address = forms.CharField(widget=forms.Textarea, required=True)
    delivery_postcode = forms.CharField(max_length=20, required=True)
    phone = forms.CharField(max_length=20, required=True)

    class Meta:
        model = User
        fields = (
            "email",
            "account_type",
            "password1",
            "password2",
            "first_name",
            "last_name",
            "business_name",
            "business_type",
            "delivery_address",
            "delivery_postcode",
            "phone",
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        acct = cleaned.get("account_type", Profile.Role.CUSTOMER)
        bname = (cleaned.get("business_name") or "").strip()
        btype = cleaned.get("business_type", "")

        if acct in (Profile.Role.COMMUNITY_GROUP, Profile.Role.RESTAURANT) and not bname:
            self.add_error(
                "business_name",
                "Organisation / Business name is required for this account type.",
            )

        if acct == Profile.Role.RESTAURANT and not btype:
            self.add_error(
                "business_type",
                "Please select your type of establishment.",
            )

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)

        email = self.cleaned_data["email"].strip()
        first = self.cleaned_data["first_name"].strip()
        last = self.cleaned_data["last_name"].strip()
        role = self.cleaned_data["account_type"]
        bname = (self.cleaned_data.get("business_name") or "").strip()
        btype = self.cleaned_data.get("business_type", "") if role == Profile.Role.RESTAURANT else ""

        # Username from first+last (internal only)
        base = f"{first}{last}".lower().replace(" ", "")
        base = "".join(ch for ch in base if ch.isalnum() or ch in ("_", "-"))
        if not base:
            base = "user"

        base = base[:140]
        username = base
        counter = 1
        while User.objects.filter(username=username).exists():
            suffix = str(counter)
            username = f"{base[:150 - len(suffix)]}{suffix}"
            counter += 1

        user.username = username
        user.email = email
        user.first_name = first
        user.last_name = last

        if commit:
            user.save()
            Profile.objects.create(
                user=user,
                role=role,
                business_name=bname,
                business_type=btype,
                contact_first_name=first,
                contact_last_name=last,
                phone=self.cleaned_data["phone"].strip(),
                delivery_address=self.cleaned_data["delivery_address"].strip(),
                delivery_postcode=self.cleaned_data["delivery_postcode"].strip(),
            )

        return user

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].help_text = ""
        self.fields["password1"].help_text = "At least 8 characters."
        self.fields["password2"].help_text = "Re-enter the password."

class ProducerRegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)
    business_name = forms.CharField(max_length=255, required=True)
    contact_first_name = forms.CharField(max_length=255, required=True)
    contact_last_name = forms.CharField(max_length=255, required=True)
    phone = forms.CharField(max_length=20, required=True)
    address = forms.CharField(widget=forms.Textarea, required=True)
    postcode = forms.CharField(max_length=20, required=True)

    class Meta:
        model = User
        fields = (
            "email",
            "password1",
            "password2",
            "business_name",
            "contact_first_name",
            "contact_last_name",
            "phone",
            "address",
            "postcode",
        )
    
    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


    def save(self, commit=True):
        user = super().save(commit=False)

        email = self.cleaned_data["email"].strip()
        business_name = self.cleaned_data["business_name"].strip()
        first = self.cleaned_data["contact_first_name"].strip()
        last = self.cleaned_data["contact_last_name"].strip()

        base = business_name.lower().replace(" ", "")
        base = "".join(ch for ch in base if ch.isalnum() or ch in ("_", "-"))
        if not base:
            base = "producer"
        base = base[:140]  # leave room for numeric suffix
        username = base
        counter = 1
        while User.objects.filter(username=username).exists():
            suffix = str(counter)
            username = f"{base[:150 - len(suffix)]}{suffix}"
            counter += 1

        user.username = username
        user.email = email
        user.first_name = first
        user.last_name = last

        if commit:
            user.save()
            Profile.objects.create(
                user=user,
                role=Profile.Role.PRODUCER,
                business_name=business_name,
                contact_first_name=first,
                contact_last_name=last,
                phone=self.cleaned_data["phone"].strip(),
                address=self.cleaned_data["address"].strip(),
                postcode=self.cleaned_data["postcode"].strip(),
            )

        return user

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].help_text = ""
        self.fields["password1"].help_text = "At least 8 characters."
        self.fields["password2"].help_text = "Re-enter the password."
