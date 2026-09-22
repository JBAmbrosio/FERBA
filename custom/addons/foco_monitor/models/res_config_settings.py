from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    """Seccion de Foco dentro de Ajustes.

    Es donde la gente espera encontrar los permisos de una aplicacion, asi que
    los accesos se administran desde aqui ademas de desde Foco > Configuracion.
    Las dos pantallas leen y escriben los MISMOS grupos, de modo que no pueden
    contradecirse.
    """

    _inherit = 'res.config.settings'

    foco_user_ids = fields.Many2many(
        'res.users', 'foco_cfg_ver_rel', 'config_id', 'user_id',
        string='Pueden ver Foco')
    foco_manager_ids = fields.Many2many(
        'res.users', 'foco_cfg_admin_rel', 'config_id', 'user_id',
        string='Administran Foco')

    foco_retention_months = fields.Integer(
        string='Conservar el detalle (meses)',
        help='0 = no se borra nada, nunca. Con un numero, un proceso diario '
             'elimina el detalle de uso y los periodos sin actividad mas '
             'antiguos que esos meses. El borrado no se puede deshacer.')
    foco_retention_note = fields.Char(
        string='Estado de la purga', readonly=True)

    foco_block_enabled = fields.Boolean(
        string='Aplicar bloqueo de sitios',
        help='Interruptor general. Apagado, los equipos limpian la lista que '
             'tuvieran puesta.')
    foco_default_policy_id = fields.Many2one(
        'foco.policy', string='Perfil por omision')

    foco_document_enabled = fields.Boolean(
        string='Reportar el archivo abierto',
        help='Interruptor general. Apagado, ningun equipo reporta el nombre de '
             'ningun archivo, sin importar que aplicaciones esten marcadas.')

    foco_screenshot_enabled = fields.Boolean(
        string='Permitir capturas de pantalla',
        help='Interruptor general. Apagado, ningun equipo toma ni guarda una '
             'sola captura.')
    foco_screenshot_unclassified_minutes = fields.Integer(
        string='Minutos en una app sin clasificar')
    foco_screenshot_retention_days = fields.Integer(
        string='Conservar las capturas (dias)')

    def _foco_grupos(self):
        ref = self.env.ref
        return (ref('foco_monitor.group_foco_user', raise_if_not_found=False),
                ref('foco_monitor.group_foco_manager', raise_if_not_found=False))

    @api.model
    def get_values(self):
        valores = super().get_values()
        g_ver, g_admin = self._foco_grupos()
        ajustes = self.env['foco.settings'].sudo().get_settings()
        valores.update(
            foco_user_ids=[(6, 0, g_ver.user_ids.ids)] if g_ver else [],
            foco_manager_ids=[(6, 0, g_admin.user_ids.ids)] if g_admin else [],
            foco_retention_months=ajustes.retention_months,
            foco_retention_note=self._foco_nota_purga(ajustes),
            foco_block_enabled=ajustes.block_enabled,
            foco_default_policy_id=ajustes.default_policy_id.id or False,
            foco_document_enabled=ajustes.document_enabled,
            foco_screenshot_enabled=ajustes.screenshot_enabled,
            foco_screenshot_unclassified_minutes=ajustes.screenshot_unclassified_minutes,
            foco_screenshot_retention_days=ajustes.screenshot_retention_days,
        )
        return valores

    @api.model
    def _foco_nota_purga(self, ajustes):
        """Que ha hecho la purga en realidad, en una frase.

        Una pantalla que solo dice "conservar 12 meses" no permite distinguir
        entre un proceso que corre y uno que nunca arranco.
        """
        if not ajustes.retention_months:
            return 'Apagada: no se borra nada.'
        if not ajustes.retention_last_run:
            return 'Configurada, pero todavia no ha corrido.'
        cuando = fields.Datetime.context_timestamp(
            self, ajustes.retention_last_run).strftime('%d/%m/%Y %H:%M')
        nota = 'Ultima corrida %s: %d renglones borrados.' % (
            cuando, ajustes.retention_last_deleted)
        if ajustes.retention_pending:
            nota += ' Quedan %d por borrar en las siguientes corridas.' % (
                ajustes.retention_pending,)
        return nota

    def action_foco_alcance(self):
        return self.env['foco.settings'].action_alcance()

    def set_values(self):
        super().set_values()
        g_ver, g_admin = self._foco_grupos()
        # sudo: quien administra Foco no tiene por que poder editar grupos de
        # Odoo en general; el permiso se acota a estos dos grupos.
        if g_ver:
            g_ver.sudo().user_ids = [(6, 0, self.foco_user_ids.ids)]
        if g_admin:
            g_admin.sudo().user_ids = [(6, 0, self.foco_manager_ids.ids)]
        meses = self.foco_retention_months or 0
        self.env['foco.settings'].sudo().get_settings().write({
            'retention_months': max(meses, 0),
            'block_enabled': self.foco_block_enabled,
            'default_policy_id': self.foco_default_policy_id.id or False,
            'document_enabled': self.foco_document_enabled,
            'screenshot_enabled': self.foco_screenshot_enabled,
            # max(...,0): un negativo aqui apagaria el disparador de una forma
            # que nadie entenderia al leer «-5 minutos» en la pantalla.
            'screenshot_unclassified_minutes': max(
                self.foco_screenshot_unclassified_minutes or 0, 0),
            'screenshot_retention_days': max(
                self.foco_screenshot_retention_days or 0, 0),
        })
