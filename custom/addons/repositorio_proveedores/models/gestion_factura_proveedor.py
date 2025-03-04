import xml.etree.ElementTree as ET
import json
import logging
import base64
from odoo import models, fields, api


_logger = logging.getLogger(__name__)
 
class RepositorioProveedor(models.Model):
    _inherit = 'purchase.order' 
    

    @api.onchange('x_studio_factura_xml')
    def _onchange_factura_xml(self):
        if self.x_studio_factura_xml:
            #Llamar al método para crear o actualizar el registro
            self.create_record_bucket()

    @api.model        
    def create_record_bucket(self):
     
        # Available variables
        Factura_xml = self.x_studio_factura_xml
        Factura_pdf = self.x_studio_factura_pdf
        id_doc = self.id
        proveedor = self.partner_id
        fecha_de_factura = self.x_fecha_de_factura
        folio_de_factura = self.x_folio_de_factura

        #se inicia la busqueda para evitar duplicados de registro
        gestion = self.env['x_gestion_de_factura_p'].search([('x_studio_orden_de_compra','=',id_doc)])

        try:

            if gestion:
                try:
                    repositorio_proveedores = gestion.write({
                                                    'x_name':proveedor.name,
                                                    'x_studio_proveedor':proveedor.id,
                                                    'x_studio_orden_de_compra':id_doc,
                                                    'x_fecha_factura': fecha_de_factura,
                                                    'x_folio_factura':folio_de_factura,
                                                })
                    
                    if repositorio_proveedores:
                        self.message_post(partner_ids=[self.env.user.partner_id.id], 
                                           body=f"Repositorio {gestion.x_name} actualizado.",
                                           subject="Notificación")
                                            
                except Exception as e:
                   
                    self.message_post(partner_ids=[self.env.user.partner_id.id], 
                                        body=f"Error al actualizar repositorio proveedores: {str(e)} ",
                                        subject="Notificación")


                
            else:

                try:
                    repositorio_proveedores = self.env['x_gestion_de_factura_p'].create({
                                                    'x_studio_orden_de_compra':id_doc,
                                                    'x_studio_proveedor':proveedor.id,
                                                    #'x_studio_proyecto': proyecto,
                                                    #'c_studio_presupuesto': presupuesto,
                                                    'x_fecha_factura': fecha_de_factura,
                                                    'x_folio_factura':folio_de_factura,
                                                    'x_name':'repositorio',
                                                    
                
                
                                                })
                    if repositorio_proveedores:
                       
                        self.message_post(partner_ids=[self.env.user.partner_id.id],
                                            body="Reporitorio de proveedores actualizado",
                                            subject="Notificación" )

                        
                
                except Exception as e:
                    self.message_post(partner_ids=[self.env.user.partner_id.id],
                                        body=f"Error al crear registro en repositorio proveedores: {str(e)}  ",
                                        subject="Notificación" )

                    
            
            
        except Exception as e:
            self.message_post(partner_ids=[self.env.user.partner_id.id],
                                body=f"Error: {str(e)}",
                                subject="Notificación" )
 
            

class GestionFacturaProveedor(models.Model): 
    _inherit = 'x_gestion_de_factura_p'

    @api.model
    def create(self, vals):

        record = super(GestionFacturaProveedor, self).create(vals)
        
        if record:
            _logger.info(f">>> Registro creado con ID {record.id}, llamando a read_invoice()")

            # Llamar al método para procesar el XML después de crear el registro
            record.read_invoice()
        
        return record
    
    @api.model 
    def read_invoice(self): 
    
        
        if not self.x_studio_orden_de_compra:
            
            _logger.info(">>> No hay orden de compra relacionada.")
            return

        # Buscar la orden de compra relacionada
        purchase_order = self.env['purchase.order'].search([('id', '=', self.x_studio_orden_de_compra.id)], limit=1)
        
        if not purchase_order:
            _logger.info(">>> No se encontró la orden de compra en purchase.order.")
            return

        _logger.info(f">>> Procesando XML de la orden de compra {purchase_order.id}")

            
       
        try:
            #Factura_xml = self.x_studio_factura_xml
            # Decodificar el XML (si está en binario)
            xml_data = purchase_order.x_studio_factura_xml.decode('base64')  # Decodificar el archivo
            
            #xml_str = Factura_xml.decode('utf-8') if isinstance(Factura_xml, bytes) else Factura_xml

            # Parsear el XML
            root = ET.fromstring(xml_data)  # Parsear el XML correctamente

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

                
                self.message_post(partner_ids=[self.env.user.partner_id.id],  
                                        body="XML procesado y convertido a JSON correctamente.",
                                        subject="Notificación")


        except Exception as e:
            self.message_post(partner_ids=[self.env.user.partner_id.id], 
                                body=f"Error procesando XML: {str(e)}",
                                subject="Notificación")
       

            
   

        














   
