"""Custom user keyed on phone, plus the Membership that grants tenant access."""

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel

__all__ = ['Membership', 'Role', 'User', 'UserManager']


class Role(models.TextChoices):
    OWNER = 'OWNER', 'Owner'
    PRACTITIONER = 'PRACTITIONER', 'Practitioner'
    STAFF = 'STAFF', 'Staff'


#: Roles allowed to read clinical narrative and prescriptions (SPEC §6.1).
CLINICAL_ROLES = frozenset({Role.OWNER, Role.PRACTITIONER})


class UserManager(BaseUserManager):
    """Phone is the identifier; there is no username column."""

    use_in_migrations = True

    def create_user(self, phone: str, password: str | None = None, **extra):
        if not phone:
            raise ValueError('A phone number is required.')
        user = self.model(phone=phone, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, password: str | None = None, **extra):
        extra.setdefault('is_staff', True)
        extra.setdefault('is_superuser', True)
        if not extra['is_staff'] or not extra['is_superuser']:
            raise ValueError('A superuser must have is_staff and is_superuser set.')
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Deliberately not organization-scoped.

    Phone is the login identifier so it must be globally unique, and one
    practitioner working at two clinics on one deployment holds one account with
    two memberships. Membership is the tenancy join.
    """

    phone = models.CharField(max_length=32, unique=True)
    email = models.EmailField(blank=True)
    full_name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text='Django admin access.')
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = 'phone'
    REQUIRED_FIELDS = ['full_name']

    class Meta:
        ordering = ['full_name']

    def __str__(self) -> str:
        return f'{self.full_name} ({self.phone})'

    def get_short_name(self) -> str:
        return self.full_name.split(' ')[0] if self.full_name else self.phone

    def get_full_name(self) -> str:
        return self.full_name


class Membership(TimeStampedModel):
    """A user's role within one organization.

    Not an ``OrgOwnedModel``: the middleware queries this to *establish* the
    active organization, so it has to be readable before any org is active.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='memberships')
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['organization__name', 'user__full_name']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'organization'], name='membership_unique_per_org'
            )
        ]

    def __str__(self) -> str:
        return f'{self.user.full_name} — {self.role} @ {self.organization.name}'

    @property
    def can_view_clinical(self) -> bool:
        """MVP: replace with permission layer (SPEC §6.1 RolePermission)."""
        return self.role in CLINICAL_ROLES

    @property
    def is_owner(self) -> bool:
        """MVP: replace with permission layer (SPEC §6.1 RolePermission)."""
        return self.role == Role.OWNER
