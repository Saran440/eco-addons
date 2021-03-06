# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


INCOME_TAX_FORM = [('pnd1', 'PND1'),
                   ('pnd3', 'PND3'),
                   ('pnd3a', 'PND3a'),
                   ('pnd53', 'PND53'),
                   ('pnd54', 'PND54')]


WHT_CERT_INCOME_TYPE = [('1', '1. เงินเดือน ค่าจ้าง ฯลฯ 40(1)'),
                        ('2', '2. ค่าธรรมเนียม ค่านายหน้า ฯลฯ 40(2)'),
                        ('3', '3. ค่าแห่งลิขสิทธิ์ ฯลฯ 40(3)'),
                        ('5', '5. ค่าจ้างทำของ ค่าบริการ ฯลฯ 3 เตรส'),
                        ('6', '6. ค่าบริการ/ค่าสินค้าภาครัฐ'),
                        ('7', '7. ค่าจ้างทำของ ค่ารับเหมา'),
                        ('8', '8. ธุรกิจพาณิชย์ เกษตร อื่นๆ')]


TAX_PAYER = [('withholding', 'Withholding'),
             ('paid_one_time', 'Paid One Time')]


class WithholdingTaxCert(models.Model):
    _name = 'withholding.tax.cert'

    name = fields.Char(
        string='Number',
        readonly=True,
        related='payment_id.name',
        store=True,
    )
    date = fields.Date(
        string='Date',
        required=True,
        readonly=True,
        copy=False,
        states={'draft': [('readonly', False)]},
    )
    state = fields.Selection(
        [('draft', 'Draft'),
         ('done', 'Done'),
         ('cancel', 'Cancelled')],
        string='Status',
        default='draft',
        copy=False,
    )
    payment_id = fields.Many2one(
        comodel_name='account.payment',
        string='Payment',
        copy=False,
        readonly=True,
        states={'draft': [('readonly', False)]},
        domain="[('partner_id', '=', supplier_partner_id)]",
        ondelete='restrict',
    )
    company_partner_id = fields.Many2one(
        'res.partner',
        string='Company',
        readonly=True,
        copy=False,
        default=lambda self: self.env.user.company_id.partner_id,
        ondelete='restrict',
    )
    supplier_partner_id = fields.Many2one(
        'res.partner',
        string='Supplier',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        copy=False,
        ondelete='restrict',
    )
    company_taxid = fields.Char(
        related='company_partner_id.vat',
        string='Company Tax ID',
        readonly=True,
    )
    supplier_taxid = fields.Char(
        related='supplier_partner_id.vat',
        string='Supplier Tax ID',
        readonly=True,
    )
    income_tax_form = fields.Selection(
        INCOME_TAX_FORM,
        string='Income Tax Form',
        required=True,
        readonly=True,
        copy=False,
        states={'draft': [('readonly', False)]},
    )
    wt_line = fields.One2many(
        'withholding.tax.cert.line',
        'cert_id',
        string='Withholding Line',
        readonly=True,
        states={'draft': [('readonly', False)]},
        copy=False,
    )
    tax_payer = fields.Selection(
        TAX_PAYER,
        string='Tax Payer',
        default='withholding',
        required=True,
        readonly=True,
        states={'draft': [('readonly', False)]},
        copy=False,
    )

    @api.constrains('wt_line')
    def _check_wt_line(self):
        for cert in self:
            for line in cert.wt_line:
                if line.amount != line.base * line.wt_percent / 100:
                    raise ValidationError(_('WT Base/Percent/Tax mismatch!'))

    @api.onchange('payment_id')
    def _onchange_payment_id(self):
        """ Prepare withholding cert """
        wt_account_ids = self._context.get('wt_account_ids', [])
        self.date = self.payment_id.payment_date
        self.supplier_partner_id = self.payment_id.partner_id
        # Hook to find wt move lines
        wt_move_lines = self._get_wt_move_line(self.payment_id, wt_account_ids)
        CertLine = self.env['withholding.tax.cert.line']
        for line in wt_move_lines:
            self.wt_line += CertLine.new(self._prepare_wt_line(line))

    @api.model
    def _prepare_wt_line(self, move_line):
        """ Hook point to prepare wt_line """
        # With withholding.tax.move, get % tax
        WTMove = self.env['withholding.tax.move']
        wt_move = WTMove.search([('wt_account_move_id', '=',
                                  move_line.move_id.id)])
        base = 0.0
        percent = 0.0
        tax = abs(move_line.balance)
        if wt_move:
            if wt_move.statement_id.base:
                percent = round(wt_move.statement_id.tax /
                                wt_move.statement_id.base, 2)
                if percent:
                    base = tax / percent
        vals = {'wt_percent': percent * 100,
                'base': base,
                'amount': abs(move_line.balance),
                'ref_move_line_id': move_line.id}
        return vals

    @api.model
    def _get_wt_move_line(self, payment, wt_account_ids):
        """ Hook point to get wt_move_lines """
        # Move line from payment itself
        wt_move_lines = payment.move_line_ids
        # From other related withholding move line (l10n_it_withholding_tax)
        if 'withholding_tax_generated_by_move_id' in payment.move_line_ids:
            payment_moves = payment.move_line_ids.mapped('move_id')
            wt_moves = self.env['account.move.line'].search([
                ('withholding_tax_generated_by_move_id', 'in',
                 payment_moves.ids)]).mapped('move_id')
            wt_move_lines += wt_moves.mapped('line_ids')
        return wt_move_lines.\
            filtered(lambda l: l.account_id.id in wt_account_ids)

    @api.multi
    def action_draft(self):
        self.write({'state': 'draft'})
        return True

    @api.multi
    def action_done(self):
        self.write({'state': 'done'})
        return True

    @api.multi
    def action_cancel(self):
        self.write({'state': 'cancel'})
        return True


class WithholdingTaxCertLine(models.Model):
    _name = 'withholding.tax.cert.line'

    cert_id = fields.Many2one(
        'withholding.tax.cert',
        string='WHT Cert',
        index=True,
    )
    wt_cert_income_type = fields.Selection(
        WHT_CERT_INCOME_TYPE,
        string='Type of Income',
        required=True,
    )
    wt_cert_income_desc = fields.Char(
        string='Income Description',
        size=500,
        required=False,
    )
    base = fields.Float(
        string='Base Amount',
        readonly=False,
    )
    wt_percent = fields.Float(
        string='% Tax',
    )
    amount = fields.Float(
        string='Tax Amount',
        readonly=False,
    )
    ref_move_line_id = fields.Many2one(
        comodel_name='account.move.line',
        string='Ref Journal Item',
        readonly=True,
        help="Reference back to journal item which create wt move",
    )

    @api.onchange('wt_cert_income_type')
    def _onchange_wt_cert_income_type(self):
        if self.wt_cert_income_type:
            select_dict = dict(WHT_CERT_INCOME_TYPE)
            self.wt_cert_income_desc = select_dict[self.wt_cert_income_type]
        else:
            self.wt_cert_income_desc = False

    @api.onchange('wt_percent')
    def _onchange_wt_percent(self):
        if self.base:
            self.amount = self.base * self.wt_percent / 100
        if self.amount:
            if self.wt_percent:
                self.base = self.amount * 100 / self.wt_percent
            else:
                self.base = 0.0
