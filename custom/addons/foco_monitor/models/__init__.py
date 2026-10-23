from . import foco_category
from . import foco_app
from . import foco_site
from . import foco_whatsapp
from . import foco_policy
from . import foco_policy_category
from . import foco_computer
from . import foco_usage
from . import foco_mi_dia
from . import foco_event
from . import foco_workday
from . import foco_command
from . import foco_capture
from . import foco_watch
from . import foco_mobile
from . import foco_mobile_login
from . import foco_ir_http
from . import foco_app_token
from . import foco_settings
from . import foco_openai
from . import foco_ai_usage
from . import foco_call_review
from . import foco_tomy
# Tommy GPT: asistente de IA de uso general. Usa foco.openai y foco.ai.usage.
from . import foco_gpt
from . import foco_integrity
from . import foco_integrity_verdict
from . import foco_invitation
from . import foco_absence
# Despues de foco_absence: la conversacion apunta a ese modelo.
from . import foco_absence_msg
# Pausas cortas y actividad del dia (14-oct-2026): usan la conversion de horas
# y el tablero de foco.absence.
from . import foco_pausa
from . import foco_daily_report
from . import foco_tab_review
from . import hr_employee
from . import res_config_settings
from . import res_users
from . import foco_menu_visibility
# Flujo de vendedores: el cliente es un res.partner, la oportunidad un crm.lead.
from . import res_partner
from . import foco_visita
# Despues de foco_visita: reusa su rubrica de coaching (SISTEMA_VISITA).
from . import foco_llamada
from . import foco_vendedor_import
# Viaticos, gasolina y vehiculos (apartado de campo). Usa foco.location (de
# foco_mobile) para el cruce de km con GPS, y la multiempresa/monedas.
from . import foco_viaticos
# Al final a proposito: extienden foco.settings y foco.invitation, que tienen
# que estar ya definidos cuando Odoo arma el registro. Importados antes, el
# modulo no carga ("Failed to load registry").
from . import foco_amapi
from . import foco_mobile_mdm
