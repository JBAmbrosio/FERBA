from odoo import fields, models


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

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ['foco_department_ids']

    def write(self, vals):
        """Permite a quien administra Foco fijar el alcance, y NADA mas.

        Escribir en res.users es privilegio de un administrador de Odoo, y
        pedir ese privilegio para repartir departamentos de Foco seria dar de
        mas. La excepcion se acota a este unico campo: cualquier otra cosa en
        la misma escritura la vuelve a someter a los permisos normales.
        """
        if (set(vals) == {'foco_department_ids'}
                and not self.env.su
                and self.env.user.has_group('foco_monitor.group_foco_manager')):
            return super(ResUsers, self.sudo()).write(vals)
        return super().write(vals)
