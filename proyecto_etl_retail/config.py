"""
config.py — Parámetros y reglas compartidas del módulo del Ingeniero/a 1.

Todo lo que pueda cambiar cuando lleguen los archivos reales (nombres de columnas,
tokens de "nulo", factor IQR) está aquí para no tocar la lógica en calidad.py.
"""

# ----------------------------------------------------------------------------
# Parámetros estadísticos
# ----------------------------------------------------------------------------
FACTOR_IQR = 1.5            # Fase 3: límites = Q1 - 1.5*IQR  y  Q3 + 1.5*IQR
ALFA = 0.05                 # Nivel de significancia para clasificar MCAR/MAR
MAX_CATEGORIAS_TEST = 20    # Columnas categóricas con más valores únicos no se usan como explicativas

# Solo informativo: la conversión USD->MXN la hace el Ingeniero/a 2 (Fase 4).
TASA_USD_MXN = 19.50

# ----------------------------------------------------------------------------
# Esquema canónico (nombres internos del pipeline)
# ----------------------------------------------------------------------------
COLUMNAS_CANONICAS = [
    "id_venta", "fecha", "id_producto", "cantidad", "precio_unitario",
    "monto_total", "moneda", "id_cliente", "email", "metodo_pago", "sucursal", "estatus_pedido",
]
COLUMNAS_OBLIGATORIAS = ["id_venta", "fecha", "id_producto", "cantidad", "precio_unitario", "monto_total"]
COLUMNAS_NUMERICAS = ["cantidad", "precio_unitario", "monto_total"]

# Columnas de la fuente que NO forman parte del esquema analítico (dato personal sin uso en el modelo).
# Se perfilan en la Fase 1 pero no viajan a transacciones_limpias.
COLUMNAS_FUERA_DE_ESQUEMA = ["direccion_envio"]

# Epoch (segundos/milisegundos) -> fecha local. México (Colima) = UTC-6 todo el año (sin horario de verano desde 2022).
HORAS_UTC_EPOCH = -6

# Llaves: NUNCA se imputan (inventar una llave crearía relaciones falsas).
COLUMNAS_LLAVE = ["id_venta", "id_producto"]

# Categóricas que se imputan con 'ANONIMO'
COLUMNAS_ANONIMO = ["id_cliente", "email"]
ETIQUETA_ANONIMO = "ANONIMO"
ETIQUETA_DESCONOCIDO = "DESCONOCIDO"   # resto de categóricas (metodo_pago, sucursal, ...)

# Moneda
MONEDA_DEFAULT = "MXN"
MAPA_MONEDA = {
    "PESOS": "MXN", "PESO": "MXN", "MN": "MXN", "MXP": "MXN", "MXN": "MXN", "M.N.": "MXN",
    "DOLARES": "USD", "DOLAR": "USD", "DLS": "USD", "DLLS": "USD", "US$": "USD", "USD": "USD",
}

# Textos que en realidad significan "dato faltante" (se comparan en minúsculas y sin espacios extremos)
TOKENS_NULOS = {
    "", "n/a", "na", "n.a.", "null", "none", "nan", "nil", "-", "--", "---",
    "s/d", "sd", "sin dato", "sin datos", "?", "nulo", "vacio", "vacío", "undefined",
}

# ----------------------------------------------------------------------------
# Alias de columnas: nombre canónico -> posibles nombres en los archivos reales.
# Se comparan "normalizados" (minúsculas, sin acentos, símbolos -> '_').
# >>> Si tus archivos reales usan otros nombres, agrégalos aquí. <<<
# ----------------------------------------------------------------------------
ALIAS_COLUMNAS = {
    "id_venta": ["id_venta", "ticket", "folio", "id_ticket", "num_ticket", "order_id", "id_orden",
                 "id_pedido", "pedido_id", "id_transaccion", "transaccion_id", "venta_id", "id"],
    "fecha": ["fecha", "fecha_venta", "fecha_pedido", "fecha_compra", "date", "order_date", "order_timestamp",
              "created_at", "timestamp", "fecha_hora", "fecha_transaccion"],
    "id_producto": ["id_producto", "sku", "producto_id", "product_id", "codigo_producto", "cod_producto",
                    "item_id", "clave_producto", "id_sku", "producto"],
    "cantidad": ["cantidad", "qty", "quantity", "unidades", "units", "cant", "piezas"],
    "precio_unitario": ["precio_unitario", "precio", "unit_price", "price", "precio_unit", "pu", "precio_venta"],
    "monto_total": ["monto_total", "total", "importe", "amount", "total_venta", "monto", "total_amount",
                    "importe_total", "subtotal_total", "monto_total_mxn"],
    "moneda": ["moneda", "currency", "divisa", "tipo_moneda", "pago_moneda"],
    "id_cliente": ["id_cliente", "cliente_id", "customer_id", "cliente", "id_customer", "cliente_cliente_id"],
    "email": ["email", "correo", "correo_electronico", "e_mail", "mail", "customer_email",
              "cliente_email", "email_cliente", "correo_cliente"],
    "metodo_pago": ["metodo_pago", "payment_method", "payment_gateway", "pasarela_pago", "forma_pago", "tipo_pago",
                    "pago_metodo", "metodo"],
    "sucursal": ["sucursal", "tienda", "punto_venta", "store", "branch"],
    "estatus_pedido": ["estatus_pedido", "order_status", "estatus", "estado_pedido", "status"],
    "direccion_envio": ["direccion_envio", "shipping_address", "direccion", "domicilio_envio"],
}


# ----------------------------------------------------------------------------
# Fases 4-7 (Ing. 2 y Ing. 3)
# ----------------------------------------------------------------------------
# Catálogo maestro: nombre canónico -> posibles nombres en catalogo_productos.csv
ALIAS_CATALOGO = {
    "id_producto": ["id_producto", "sku", "codigo", "codigo_producto", "clave", "clave_producto", "producto_id", "id"],
    "nombre_producto": ["nombre_producto", "nombre", "descripcion", "producto", "name"],
    "categoria": ["categoria", "category", "linea", "departamento", "familia"],
    "precio_lista": ["precio_lista", "precio", "price", "precio_unitario", "precio_catalogo", "precio_base_mxn", "precio_base"],
}

# Discretización del importe (Fase 5)
ETIQUETAS_SEGMENTO = ["Bajo", "Medio", "Alto"]
CUANTILES_SEGMENTO = (1 / 3, 2 / 3)     # terciles del monto_total en MXN

# Validación (Fase 6)
FECHA_MINIMA = "2000-01-01"
CANTIDAD_MAXIMA = 10_000                # cota de cordura (tras el capping nunca debería alcanzarse)
TOLERANCIA_MONTO = 0.01                 # |monto - cantidad*precio| <= max(0.01, 1 %)

# Base de datos (Fase 7)
NOMBRE_BD = "ventas_retail.db"
