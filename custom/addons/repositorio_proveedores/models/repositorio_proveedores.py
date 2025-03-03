import xml.etree.ElementTree as ET
import json
import logging
from odoo import models, api

_logger = logging.getLogger(__name__)
 

class GestionFacturaProveedor(models.Model):
    _inherit = 'x_gestion_de_factura_proveedor' 

    @api.model
    def create(self, vals):
        record = super(GestionFacturaProveedor, self).create(vals)
        
        # Llamar al método para procesar el XML después de crear el registro
        record.read_invoice()
        
        return record
    
    def read_invoice(self):
        _logger.info(">>> Ejecutando read_invoice en x_gestion_de_factura_proveedor")

        if self.x_studio_factura_xml:

       
            try:
                Factura_xml = self.x_studio_factura_xml
                # Decodificar el XML (si está en binario)
                xml_str = Factura_xml.decode('utf-8') if isinstance(Factura_xml, bytes) else Factura_xml

                # Parsear el XML
                root = ET.fromstring(xml_str)

                # Espacio de nombres del CFDI 4.0
                ns = {
                    'cfdi': 'http://www.sat.gob.mx/cfd/4',
                    'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'
                }

                cfdi = []
                
                # Buscar el nodo Comprobante
                for comprobante in root.findall('.//cfdi:Comprobante', ns):
                    comprobante_data = {
                        "Version": comprobante.get("Version"),
                        "Sello": comprobante.get("Sello"),
                        "Fecha": comprobante.get("Fecha"),
                        "CondicionesDePago": comprobante.get("CondicionesDePago"),
                        "Folio": comprobante.get("Folio"),
                        "Serie": comprobante.get("Serie"),
                        "FormaPago": comprobante.get("FormaPago"),
                        "SubTotal": float(comprobante.get("SubTotal") or 0.0),
                        "Total": float(comprobante.get("Total") or 0.0),
                        "Moneda": comprobante.get("Moneda"),
                        "TipoDeComprobante": comprobante.get("TipoDeComprobante"),
                        "Exportacion": comprobante.get("Exportacion"),
                        "MetodoPago": comprobante.get("MetodoPago"),
                        "LugarExpedicion": comprobante.get("LugarExpedicion"),
                        "Emisor": {},
                        "Receptor": {},
                        "Complemento": {}
                    }

                    # Buscar Emisor
                    emisor = comprobante.find('.//cfdi:Emisor', ns)
                    if emisor is not None:
                        comprobante_data["Emisor"] = {
                            "Rfc": emisor.get("Rfc"),
                            "Nombre": emisor.get("Nombre"),
                            "RegimenFiscal": emisor.get("RegimenFiscal")
                        }

                    # Buscar Receptor
                    receptor = comprobante.find('.//cfdi:Receptor', ns)
                    if receptor is not None:
                        comprobante_data["Receptor"] = {
                            "Rfc": receptor.get("Rfc"),
                            "Nombre": receptor.get("Nombre"),
                            "DomicilioFiscalReceptor": receptor.get("DomicilioFiscalReceptor"),
                            "RegimenFiscalReceptor": receptor.get("RegimenFiscalReceptor"),
                            "UsoCFDI": receptor.get("UsoCFDI")
                        }

                    # Buscar Complemento (Timbre Fiscal)
                    complemento = comprobante.find('.//cfdi:Complemento', ns)
                    if complemento is not None:
                        timbrefiscal = complemento.find('.//tfd:TimbreFiscalDigital', ns)
                        if timbrefiscal is not None:
                            comprobante_data["Complemento"] = {
                                "UUID": timbrefiscal.get("UUID"),
                                "FechaTimbrado": timbrefiscal.get("FechaTimbrado"),
                                "RfcProvCertif": timbrefiscal.get("RfcProvCertif"),
                                "SelloCFD": timbrefiscal.get("SelloCFD"),
                                "NoCertificadoSAT": timbrefiscal.get("NoCertificadoSAT"),
                                "SelloSAT": timbrefiscal.get("SelloSAT"),
                                "Version": timbrefiscal.get("Version")
                            }

                    # Agregar el comprobante procesado a la lista
                    cfdi.append(comprobante_data)

                # Convertir a JSON
                json_data = json.dumps(cfdi, indent=4, ensure_ascii=False)

                # Guardar el JSON en un campo personalizado en Odoo
                self.write({"x_studio_json_factura":json_data})   

                self.message_post(body="XML procesado y convertido a JSON correctamente.")

            except Exception as e:
                self.message_post(body=f"Error procesando XML: {str(e)}")
        else:
            self.message_post(body="No se encontró archivo XML.")

       
