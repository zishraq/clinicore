"""Issuing a bill is what takes stock off the shelf, and voiding puts it back.

The invoice is the stock event (docs/adr/0009-ledger-based-stock.md), so these
tests live with billing rather than inventory: what is under test is the seam
between the two, not the ledger's own arithmetic.

The guarantee that needs the most care is *exactly once, across the
create/update boundary*. A bill can acquire its first stock line either way —
issued with one, or issued fee-only and edited afterwards — and neither route
may post twice or post nothing.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, InvoiceItem, LineType
from billing.services import InvoiceLocked, update_invoice
from billing.tests.conftest import invoice_payload
from catalog.models import Product
from core.context import organization_context
from inventory import services as inventory
from inventory.models import MovementType, StockBatch, StockMovement
from organizations.models import Branch

pytestmark = pytest.mark.django_db


@pytest.fixture
def tracked_product(organization) -> Product:
    """A product the ledger actually follows, unlike the billing fixture's."""
    with organization_context(organization):
        return Product.objects.create(
            organization=organization,
            name='Amoxicillin 250mg',
            unit='Capsule',
            sale_price=Decimal('12.00'),
            is_stock_tracked=True,
            is_sellable=True,
        )


@pytest.fixture
def shelf(organization, branch, practitioner, tracked_product):
    """Fifty on the shelf at the only branch, one batch, no expiry."""
    with organization_context(organization):
        inventory.receive_stock(
            organization,
            branch=branch,
            actor=practitioner,
            lines=[
                {
                    'product': tracked_product,
                    'quantity': Decimal('50'),
                    'cost_price': Decimal('4.00'),
                }
            ],
        )
    return tracked_product


@pytest.fixture
def dated_shelf(organization, branch, practitioner, tracked_product):
    """One lot already expired, one good for a year. FEFO would skip both ways.

    Returns them by lot number so a test can name the one it means.
    """
    with organization_context(organization):
        inventory.receive_stock(
            organization,
            branch=branch,
            actor=practitioner,
            lines=[
                {
                    'product': tracked_product,
                    'quantity': Decimal('20'),
                    'lot_number': 'GONE',
                    'expiry_date': timezone.localdate() - timedelta(days=3),
                },
                {
                    'product': tracked_product,
                    'quantity': Decimal('20'),
                    'lot_number': 'SOON',
                    'expiry_date': timezone.localdate() + timedelta(days=20),
                },
                {
                    'product': tracked_product,
                    'quantity': Decimal('20'),
                    'lot_number': 'LATER',
                    'expiry_date': timezone.localdate() + timedelta(days=365),
                },
            ],
        )

    def _lot(lot_number):
        # Opens its own context: the fixture's has closed by the time a test
        # calls this, and the manager refuses an unscoped query on purpose.
        with organization_context(organization):
            return StockBatch.objects.for_organization(organization).get(
                lot_number=lot_number
            )

    return _lot


def _product_line(product, *, index=1, quantity='10', batch=''):
    """One stock-tracked formset row, ready to merge into a payload."""
    return {
        f'items-{index}-display_name': product.name,
        f'items-{index}-line_type': LineType.PRODUCT,
        f'items-{index}-product': str(product.pk),
        f'items-{index}-batch': str(batch),
        f'items-{index}-quantity': quantity,
        f'items-{index}-unit_price': '12.00',
        f'items-{index}-discount': '0',
        f'items-{index}-sort_order': str(index),
    }


def _sale_movements(organization, invoice):
    with organization_context(organization):
        return list(
            StockMovement.objects.for_organization(organization)
            .filter(invoice_item__invoice=invoice, movement_type=MovementType.SALE)
            .order_by('pk')
        )


def _on_hand(organization, product, branch):
    with organization_context(organization):
        return inventory.on_hand(organization, product=product, branch=branch)


def test_issuing_a_bill_with_a_product_line_takes_the_stock_off(
    client, practitioner, organization, patient, branch, shelf
):
    client.force_login(practitioner)
    payload = invoice_payload(
        patient, **{'items-TOTAL_FORMS': '2', **_product_line(shelf)}
    )

    response = client.post(reverse('billing:invoice_create'), payload, follow=True)
    assert response.status_code == 200

    invoice = Invoice.all_objects.get(organization=organization)
    movements = _sale_movements(organization, invoice)
    assert len(movements) == 1
    assert movements[0].quantity == Decimal('-10.00')
    assert _on_hand(organization, shelf, branch) == Decimal('40.00')


def test_a_fee_only_bill_moves_no_stock(
    client, practitioner, organization, patient, branch, shelf
):
    """The hook is `is_stock_tracked` lines only — a consultation fee is not one."""
    client.force_login(practitioner)

    client.post(
        reverse('billing:invoice_create'), invoice_payload(patient), follow=True
    )

    invoice = Invoice.all_objects.get(organization=organization)
    assert _sale_movements(organization, invoice) == []
    assert _on_hand(organization, shelf, branch) == Decimal('50.00')


def test_a_bill_that_moved_stock_cannot_be_edited(
    client, practitioner, organization, patient, branch, shelf
):
    """Direction one: issued with a product line, so the edit never happens.

    This is how the "must not post twice" guarantee is kept on this side of the
    boundary — the ledger is append-only, so the lines freeze the moment they
    have moved anything.
    """
    client.force_login(practitioner)
    payload = invoice_payload(
        patient, **{'items-TOTAL_FORMS': '2', **_product_line(shelf)}
    )
    client.post(reverse('billing:invoice_create'), payload, follow=True)
    invoice = Invoice.all_objects.get(organization=organization)

    with organization_context(organization):
        assert invoice.has_stock_movements is True
        assert invoice.is_editable is False
        with pytest.raises(InvoiceLocked, match='append-only'):
            update_invoice(
                organization,
                actor=practitioner,
                invoice=invoice,
                form=None,
                item_formset=None,
            )

    # Still exactly one movement, and the shelf is untouched by the attempt.
    assert len(_sale_movements(organization, invoice)) == 1
    assert _on_hand(organization, shelf, branch) == Decimal('40.00')


def test_editing_a_fee_only_bill_to_add_a_product_posts_exactly_once(
    client, practitioner, organization, patient, branch, shelf
):
    """Direction two: the edit is the first thing that sells stock.

    `create_invoice` posted nothing because there was nothing tracked to post,
    so the posting call has to run on the update path too — otherwise the bill
    sells the product and the shelf never hears about it.
    """
    client.force_login(practitioner)
    client.post(
        reverse('billing:invoice_create'), invoice_payload(patient), follow=True
    )
    invoice = Invoice.all_objects.get(organization=organization)
    assert _sale_movements(organization, invoice) == []

    with organization_context(organization):
        fee = InvoiceItem.objects.get(invoice=invoice)
    payload = invoice_payload(
        patient,
        **{
            'items-TOTAL_FORMS': '2',
            'items-INITIAL_FORMS': '1',
            'items-0-id': str(fee.pk),
            'items-1-id': '',
            **_product_line(shelf),
        },
    )
    response = client.post(
        reverse('billing:invoice_update', kwargs={'pk': invoice.pk}),
        payload,
        follow=True,
    )
    assert response.status_code == 200

    movements = _sale_movements(organization, invoice)
    assert len(movements) == 1
    assert movements[0].quantity == Decimal('-10.00')
    assert _on_hand(organization, shelf, branch) == Decimal('40.00')


def test_the_posting_call_is_idempotent_when_run_again(
    client, practitioner, organization, patient, branch, shelf
):
    """The guard itself, directly: a second call posts nothing.

    The service is what makes the create/update boundary safe, so it is worth
    asserting on its own rather than only through the two routes above. A
    retry, a double-submit, or a future third caller all land here.
    """
    client.force_login(practitioner)
    payload = invoice_payload(
        patient, **{'items-TOTAL_FORMS': '2', **_product_line(shelf)}
    )
    client.post(reverse('billing:invoice_create'), payload, follow=True)
    invoice = Invoice.all_objects.get(organization=organization)

    with organization_context(organization):
        again = inventory.post_sale_movements(
            organization, invoice=invoice, actor=practitioner
        )
    assert again == []
    assert len(_sale_movements(organization, invoice)) == 1
    assert _on_hand(organization, shelf, branch) == Decimal('40.00')


def test_voiding_a_bill_puts_the_stock_back(
    client, practitioner, organization, patient, branch, shelf
):
    client.force_login(practitioner)
    payload = invoice_payload(
        patient, **{'items-TOTAL_FORMS': '2', **_product_line(shelf)}
    )
    client.post(reverse('billing:invoice_create'), payload, follow=True)
    invoice = Invoice.all_objects.get(organization=organization)
    assert _on_hand(organization, shelf, branch) == Decimal('40.00')

    client.post(
        reverse('billing:invoice_void', kwargs={'pk': invoice.pk}),
        {'reason': 'Billed the wrong patient'},
        follow=True,
    )

    assert _on_hand(organization, shelf, branch) == Decimal('50.00')
    with organization_context(organization):
        returns = StockMovement.objects.for_organization(organization).filter(
            invoice_item__invoice=invoice, movement_type=MovementType.RETURN
        )
        assert returns.count() == 1
        assert returns.first().quantity == Decimal('10.00')
        # The sale is still on the record: a compensating movement, not a delete.
        assert len(_sale_movements(organization, invoice)) == 1


def test_voiding_twice_returns_the_stock_once(
    client, practitioner, organization, patient, branch, shelf
):
    """Two clicks on void used to be harmless; with stock it would double back."""
    client.force_login(practitioner)
    payload = invoice_payload(
        patient, **{'items-TOTAL_FORMS': '2', **_product_line(shelf)}
    )
    client.post(reverse('billing:invoice_create'), payload, follow=True)
    invoice = Invoice.all_objects.get(organization=organization)

    for _ in range(2):
        client.post(
            reverse('billing:invoice_void', kwargs={'pk': invoice.pk}),
            {'reason': 'Duplicate bill'},
            follow=True,
        )

    assert _on_hand(organization, shelf, branch) == Decimal('50.00')


def test_a_line_the_shelf_cannot_cover_takes_the_whole_bill_down(
    client, practitioner, organization, patient, branch, shelf
):
    """No half-billed sale: the bill and the movements share one transaction."""
    client.force_login(practitioner)
    payload = invoice_payload(
        patient,
        **{'items-TOTAL_FORMS': '2', **_product_line(shelf, quantity='500')},
    )

    response = client.post(reverse('billing:invoice_create'), payload, follow=True)
    assert response.status_code == 200

    assert not Invoice.all_objects.filter(organization=organization).exists()
    assert _on_hand(organization, shelf, branch) == Decimal('50.00')


def test_a_named_batch_on_a_line_overrides_fefo(
    client, practitioner, organization, patient, branch, dated_shelf
):
    """The override, through the form: FEFO would have emptied GONE, then SOON."""
    later = dated_shelf('LATER')
    client.force_login(practitioner)
    payload = invoice_payload(
        patient,
        **{
            'items-TOTAL_FORMS': '2',
            **_product_line(later.product, quantity='5', batch=later.pk),
        },
    )

    response = client.post(reverse('billing:invoice_create'), payload, follow=True)
    assert response.status_code == 200

    invoice = Invoice.all_objects.get(organization=organization)
    movements = _sale_movements(organization, invoice)
    assert len(movements) == 1
    assert movements[0].batch_id == later.pk
    with organization_context(organization):
        assert dated_shelf('LATER').on_hand == Decimal('15.00')
        # Untouched, though both expire sooner.
        assert dated_shelf('SOON').on_hand == Decimal('20.00')
        assert dated_shelf('GONE').on_hand == Decimal('20.00')


def test_naming_an_expired_batch_refuses_the_whole_bill(
    client, practitioner, organization, patient, branch, dated_shelf
):
    """Expired stock is offered in the list and refused on submit.

    Nothing is written: no bill, no movement, and the invoice number is not
    burned, because the number is allocated inside the same transaction.
    """
    expired = dated_shelf('GONE')
    client.force_login(practitioner)
    payload = invoice_payload(
        patient,
        **{
            'items-TOTAL_FORMS': '2',
            **_product_line(expired.product, quantity='2', batch=expired.pk),
        },
    )

    response = client.post(reverse('billing:invoice_create'), payload, follow=True)

    assert response.status_code == 200
    messages = [str(message) for message in response.context['messages']]
    assert any('expired on' in message for message in messages), messages
    assert not Invoice.all_objects.filter(organization=organization).exists()
    with organization_context(organization):
        assert dated_shelf('GONE').on_hand == Decimal('20.00')


def test_a_batch_of_a_different_product_is_rejected_by_the_form(
    client, practitioner, organization, patient, branch, dated_shelf
):
    """The row settles what it can see; the shelf rules stay in services."""
    other_batch = dated_shelf('LATER')
    with organization_context(organization):
        other_product = Product.objects.create(
            organization=organization,
            name='Cetirizine 10mg',
            sale_price=Decimal('9.00'),
            is_stock_tracked=True,
            is_sellable=True,
        )
    client.force_login(practitioner)
    payload = invoice_payload(
        patient,
        **{
            'items-TOTAL_FORMS': '2',
            **_product_line(other_product, quantity='1', batch=other_batch.pk),
        },
    )

    response = client.post(reverse('billing:invoice_create'), payload)

    assert response.status_code == 200
    assert not Invoice.all_objects.filter(organization=organization).exists()
    formset = response.context['item_formset']
    assert any(formset.errors), formset.errors


def test_a_lot_held_at_another_branch_is_refused(
    client, practitioner, organization, patient, branch, tracked_product
):
    """The UI can offer a stale lot after the branch changes under it.

    static/js/invoice-line.js re-fetches the options when the branch select
    moves, but a form posted from a stale page still has to be refused rather
    than take stock off the wrong shelf.
    """
    with organization_context(organization):
        elsewhere = Branch.objects.create(
            organization=organization, name='Uttara Chamber', code='UTT'
        )
        inventory.receive_stock(
            organization,
            branch=elsewhere,
            actor=practitioner,
            lines=[
                {
                    'product': tracked_product,
                    'quantity': Decimal('10'),
                    'lot_number': 'UTT01',
                }
            ],
        )
        their_lot = StockBatch.objects.for_organization(organization).get(
            lot_number='UTT01'
        )
        # Stock at the bill's own branch too, so the refusal is about the lot
        # rather than about an empty shelf.
        inventory.receive_stock(
            organization,
            branch=branch,
            actor=practitioner,
            lines=[{'product': tracked_product, 'quantity': Decimal('10')}],
        )

    client.force_login(practitioner)
    payload = invoice_payload(
        patient,
        branch=str(branch.pk),
        **{
            'items-TOTAL_FORMS': '2',
            **_product_line(tracked_product, quantity='1', batch=their_lot.pk),
        },
    )

    response = client.post(reverse('billing:invoice_create'), payload, follow=True)

    assert response.status_code == 200
    messages = [str(message) for message in response.context['messages']]
    assert any('is held at' in message for message in messages), messages
    assert not Invoice.all_objects.filter(organization=organization).exists()
    with organization_context(organization):
        assert their_lot.on_hand == Decimal('10.00')


def test_the_bill_line_rewires_its_batch_list_when_the_branch_changes(
    client, practitioner, organization, patient
):
    """A canary for the wiring, not a proof it works — check a browser for that.

    Without it the row keeps the previous branch's lots on screen and the
    practitioner picks one that the service then refuses. Both halves have to
    be present: the handler on the row, and the method behind it.
    """
    client.force_login(practitioner)
    body = client.get(reverse('billing:invoice_create')).content.decode()
    assert 'onBranchChange($event)' in body

    script = Path('static/js/invoice-line.js').read_text()
    assert 'onBranchChange(event)' in script
