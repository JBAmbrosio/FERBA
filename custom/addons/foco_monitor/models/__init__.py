from . import foco_category
from . import foco_app
from . import foco_site
from . import foco_policy
from . import foco_computer
from . import foco_usage
from . import foco_mi_dia
from . import foco_event
from . import foco_workday
from . import foco_command
from . import foco_capture
from . import foco_watch
from . import foco_mobile
from . import foco_app_token
from . import foco_settings
from . import foco_openai
from . import foco_call_review
from . import foco_tomy
from . import foco_integrity
from . import foco_invitation
from . import foco_absence
from . import hr_employee
from . import res_config_settings
from . import res_users
# Al final a proposito: extienden foco.settings y foco.invitation, que tienen
# que estar ya definidos cuando Odoo arma el registro. Importados antes, el
# modulo no carga ("Failed to load registry").
from . import foco_amapi
from . import foco_mobile_mdm
