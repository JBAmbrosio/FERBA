from odoo import fields, models
from odoo.exceptions import AccessDenied
from odoo.http import request


class ResUsers(models.Model):
    """Alcance de datos de Foco por usuario.

    Controlar QUIEN entra no basta: dar acceso al jefe de un area le mostraria
    tambien el resto de la empresa. El alcance se decide aqui de forma
    explicita y no se deduce de la jerarquia, porque quien puede ver a un area
    no siempre es su responsable formal.
    """

    _inherit = 'res.users'

    foco_department_ids = fields.Many2many(
        'hr.department', 'foco_user_department_rel', 'user_id', 'department_id',
        string='Departamentos que ve en Foco',
        help='Vacio = ve todos. Con departamentos seleccionados solo vera el '
             'uso, los equipos y los periodos sin actividad de esas areas. '
             'Quien administra Foco ve todo, sin importar esta lista.')

    # A donde quiere ir el vendedor al abrir la app oficial de Odoo desde Foco
    # (el boton "Odoo" de una cotizacion en el celular). Lo consume la pantalla
    # de Odoo al arrancar dentro de la app (ver foco_ir_http, 16-oct-2026).
    foco_ir_a = fields.Char(copy=False)
    foco_ir_a_at = fields.Datetime(copy=False)

    # Acceso a Tommy GPT (el asistente de IA de uso general). Es una casilla por
    # persona que el administrador de Foco enciende sin dar acceso a nada mas:
    # por dentro agrega o quita al usuario del grupo group_foco_gpt, que es lo
    # que abre el menu y la pantalla (ver foco_gpt.py).
    foco_gpt_enabled = fields.Boolean(
        string='Acceso a Tommy GPT', compute='_compute_foco_gpt_enabled',
        inverse='_inverse_foco_gpt_enabled', search='_search_foco_gpt_enabled',
        help='Deja que esta persona use el asistente de IA de uso general, con la '
             'llave de la empresa. No le da acceso a ningun otro dato de Foco.')

    # Opt-out de los avisos OPERATIVOS por equipo (bloqueo aplicado o alterado,
    # watchdog sin ciclo, equipo sin senal). Son avisos de operacion/TI: un
    # administrador de Foco que no los atiende -el dueno, un desarrollador- no
    # tiene por que recibir el toast ni el correo de CADA maquina. Las tres
    # alertas de foco.computer respetan esta casilla (ver _destinatarios_avisos).
    # El reporte diario y el resto de Foco NO dependen de ella.
    foco_sin_avisos_operativos = fields.Boolean(
        string='No recibir avisos operativos de Foco',
        help='Deja de enviar a esta persona los avisos por equipo (bloqueo '
             'aplicado o alterado, watchdog sin ciclo, equipo sin senal). Util '
             'para el dueno o los desarrolladores, que no atienden la operacion. '
             'El reporte diario y el resto de Foco no se tocan.')

    def _grupo_gpt(self):
        return self.env.ref('foco_monitor.group_foco_gpt', raise_if_not_found=False)

    def _compute_foco_gpt_enabled(self):
        grp = self._grupo_gpt()
        for u in self:
            u.foco_gpt_enabled = bool(grp) and grp in u.all_group_ids

    def _inverse_foco_gpt_enabled(self):
        grp = self._grupo_gpt()
        if not grp:
            return
        for u in self:
            # sudo: un administrador de Foco (que no es admin de Odoo) puede
            # encender la casilla; mover la membresia del grupo necesita el
            # privilegio, acotado a este solo grupo.
            u.sudo().write({'group_ids': [(4 if u.foco_gpt_enabled else 3, grp.id)]})

    def _search_foco_gpt_enabled(self, operator, value):
        grp = self._grupo_gpt()
        if not grp:
            return [('id', '=', 0)] if (operator == '=') == bool(value) else []
        positivo = (operator in ('=', 'in')) == bool(value)
        return [('all_group_ids', 'in', grp.ids)] if positivo else [('all_group_ids', 'not in', grp.ids)]

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ['foco_department_ids', 'foco_gpt_enabled']

    def _check_credentials(self, credential, env):
        """La contraseña de siempre, y si no, el codigo de un solo uso que Foco
        le dio al celular del vendedor (foco.mobile.login). El codigo SOLO vale en
        la pagina de acceso web, de forma interactiva: ni por API ni para
        confirmar la contraseña dentro de Odoo."""
        try:
            return super()._check_credentials(credential, env)
        except AccessDenied:
            path = request.httprequest.path if request else ''
            if (credential.get('type') == 'password' and (env or {}).get('interactive', True)
                    and path.startswith('/web/login')
                    and self.env['foco.mobile.login'].canjear(self.env.user, credential.get('password') or '')):
                return {'uid': self.env.user.id, 'auth_method': 'password', 'mfa': 'default'}
            raise

    def write(self, vals):
        """Permite a quien administra Foco fijar el alcance, y NADA mas.

        Escribir en res.users es privilegio de un administrador de Odoo, y
        pedir ese privilegio para repartir departamentos de Foco seria dar de
        mas. La excepcion se acota a este unico campo: cualquier otra cosa en
        la misma escritura la vuelve a someter a los permisos normales.
        """
        campos_foco = {'foco_department_ids', 'foco_gpt_enabled',
                       'foco_sin_avisos_operativos'}
        if (vals and set(vals) <= campos_foco
                and not self.env.su
                and self.env.user.has_group('foco_monitor.group_foco_manager')):
            res = super(ResUsers, self.sudo()).write(vals)
        else:
            res = super().write(vals)
        if 'foco_department_ids' in vals:
            # Odoo guarda en cache, por usuario, el dominio de las reglas de
            # registro, y cambiar este campo no lo limpia: el alcance nuevo no
            # aplicaba hasta reiniciar el servidor (medido el 14-oct-2026).
            self.env.registry.clear_cache()
        return res
