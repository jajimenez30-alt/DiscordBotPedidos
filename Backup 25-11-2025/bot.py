# bot.py - Estructura Optimizada
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from pymongo import MongoClient
from bson.objectid import ObjectId
from discord import SelectOption, SelectMenu, Interaction, app_commands
from functools import partial

# --- 1. CARGAR CREDENCIALES ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

# --- 2. CONEXIÓN A MONGODB ---
try:
    client = MongoClient(MONGO_URI)
    db = client["CraftingBotDB"] 
    
    # Referencias globales de colecciones
    usuarios_col = db["Usuario"]
    items_col = db["Item"]
    pedidos_col = db["Pedido"]
    inventario_col = db["inventario"]
    
    print("Conexión a MongoDB exitosa. Colecciones listas.")
    
except Exception as e:
    print(f"ERROR: Falló la conexión a MongoDB. Revisa tu MONGO_URI. Detalles: {e}")
    exit()

# --- 3. CONFIGURACIÓN INICIAL DEL BOT ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True 
bot = commands.Bot(command_prefix='!', intents=intents) 

# ==============================================================================
# SECCIÓN 4: FUNCIONES SÍNCRONAS PARA MONGO (EJECUTADAS EN HILOS)
# ==============================================================================

def get_unique_categories():
    """Consulta MongoDB síncronamente para obtener categorías únicas."""
    try:
        # Consulta sincrona.
        categories = items_col.distinct("category") 
        print(f"DEBUG: Categorías encontradas: {categories}") # <-- Línea de diagnóstico
        return categories
    except Exception as e:
        # Debería capturar y mostrar cualquier error de conexión/colección
        print(f"ERROR DE MONGO (get_categories): {e}") 
        return []

def get_unique_types(category):
    """Consulta MongoDB síncronamente para obtener tipos únicos por categoría."""
    try:
        # Consulta simplificada, filtra solo por categoría.
        types = items_col.distinct("type", {"category": category}) 
        return types
    except Exception as e:
        print(f"ERROR DE MONGO (get_types): {e}") 
        return []

def get_item_details(category, item_type):
    """Obtiene todos los documentos de recetas que coinciden con la categoría y el tipo."""
    try:
        # Consulta síncrona: encuentra todos los documentos que coinciden
        recipes = items_col.find(
            {"category": category, "type": item_type},
            {"name": 1, "variations": 1, "_id": 0, "recipe_id": 1} # Proyectamos solo los campos necesarios
        )
        # Convertimos el cursor de MongoDB a una lista para enviarla fuera del thread
        return list(recipes)
    except Exception as e:
        print(f"ERROR DE MONGO (get_item_details): {e}") 
        return []
    
def update_inventory(item_name, quantity_change):
    """
    Agrega (positivo) o retira (negativo) una cantidad de un ítem en el inventario.
    Crea el ítem si no existe.
    """
    try:
        # 🟢 CORRECCIÓN: Usar inventario_col
        result = inventario_col.update_one(
            {"name": item_name},
            {"$inc": {"quantity": quantity_change}},
            upsert=True
        )
        
        # 🟢 CORRECCIÓN: Usar inventario_col
        updated_doc = inventario_col.find_one({"name": item_name})
        
        # 🟢 CORRECCIÓN: Usar inventario_col
        if updated_doc and updated_doc.get("quantity", 0) <= 0:
             inventario_col.delete_one({"name": item_name})
             return "DELETED"
             
        return "SUCCESS"

    except Exception as e:
        print(f"ERROR DE MONGO (update_inventory): {e}")
        return "ERROR"

def get_inventory_items(search_query):
    """Obtiene una lista de NOMBRES DE RECETA de la colección maestra 'item' para autocompletado."""
    try:
        # Buscamos nombres de ítems en la colección maestra 'item'
        items = items_col.find(
            {"name": {"$regex": f"^{search_query}", "$options": "i"}},
            {"name": 1, "_id": 0} # Proyectar solo el nombre
        ).limit(25)
        
        return [item['name'] for item in items]
    except Exception as e:
        print(f"ERROR DE MONGO (get_inventory_items): {e}")
        return []
    
async def inventory_item_autocomplete(interaction: discord.Interaction, current: str):
    # Execute the database search synchronously in a thread
    item_names = await bot.loop.run_in_executor(
        None,
        partial(get_inventory_items, current)
    )
    
    # Create the autocomplete choices
    return [
        app_commands.Choice(name=name, value=name)
        for name in item_names
    ]
    
def get_inventory_stock_names(search_query):
    """Obtiene NOMBRES de ítems que tienen stock de la colección 'inventario'."""
    try:
        # Consultamos directamente inventario_col y filtramos por stock > 0
        items = inventario_col.find(
            {"name": {"$regex": f"^{search_query}", "$options": "i"}, "quantity": {"$gt": 0}},
            {"name": 1, "_id": 0} 
        ).limit(25)
        
        return [item['name'] for item in items]
    except Exception as e:
        print(f"ERROR DE MONGO (get_inventory_stock_names): {e}")
        return []

async def inventory_stock_autocomplete(interaction: discord.Interaction, current: str):
    # Ejecuta la búsqueda de ítems en STOCK (inventario_col)
    item_names = await bot.loop.run_in_executor(
        None,
        partial(get_inventory_stock_names, current)
    )
    
    return [
        app_commands.Choice(name=name, value=name)
        for name in item_names
    ]

# Función que se ejecuta cuando el usuario selecciona el Nombre del Ítem (Paso 3)
async def item_name_select_callback(interaction: discord.Interaction):
    # El valor es el recipe_id (Ej: ARM_TELA_ALBA_CLERIGO)
    selected_recipe_id = interaction.data['values'][0] 
    
    # 1. Obtener la receta completa (Necesaria para extraer las variations)
    def get_full_recipe(recipe_id):
        try:
            # Buscamos por el recipe_id
            return items_col.find_one({"recipe_id": recipe_id})
        except Exception as e:
            print(f"ERROR DE MONGO (get_full_recipe): {e}")
            return None
    
    full_recipe = await bot.loop.run_in_executor(
        None,
        partial(get_full_recipe, selected_recipe_id)
    )
    
    if not full_recipe or not full_recipe.get('variations'):
        await interaction.response.edit_message(content="❌ Error: La receta no tiene niveles (variations) definidos.", view=None)
        return

    # 2. Construir las Opciones de Nivel (Ej: III, IV)
    level_options = []
    for variation in full_recipe.get('variations'):
        level_name = variation.get('level_name')
        
        # El valor ahora contiene el recipe_id y el nivel
        option_value = f"{selected_recipe_id}|{level_name}" 
        
        level_options.append(SelectOption(label=f"Nivel {level_name}", value=option_value))

    # 3. Crear el Select Menu (Paso 4: Nivel)
    select_level = discord.ui.Select(
        custom_id="select_level",
        placeholder=f"Selecciona el Nivel para {full_recipe['name']}...",
        options=level_options,
        min_values=1,
        max_values=1,
        row=0
    )
    
    # 4. Asignar el manejador de eventos
    view = discord.ui.View(timeout=180)
    view.add_item(select_level)
    select_level.callback = level_select_callback 
    
    # 5. Actualizar el mensaje
    await interaction.response.edit_message(
        content=f"**⚙️ Nuevo Pedido:**\n**Paso 4:** Selecciona el Nivel de Crafteo:", 
        view=view
    )

# Función que se ejecuta cuando el usuario selecciona el Nivel (Paso 4)
async def level_select_callback(interaction: discord.Interaction):
    
    # El valor viene como "recipe_id|level_name"
    recipe_id, level_name = interaction.data['values'][0].split('|')
    
    # 1. Obtener la receta completa (Necesaria para las quality_options)
    def get_recipe_and_variation(r_id, l_name):
        full_recipe = items_col.find_one({"recipe_id": r_id})
        if not full_recipe: return None
        
        # Encontrar el objeto 'variation' específico para el nivel
        for var in full_recipe.get('variations', []):
            if var.get('level_name') == l_name:
                return {
                    "recipe_id": r_id,
                    "level_name": l_name,
                    "name": full_recipe.get('name'),
                    "quality_options": var.get('quality_options', [])
                }
        return None
        
    recipe_data = await bot.loop.run_in_executor(
        None,
        partial(get_recipe_and_variation, recipe_id, level_name)
    )

    if not recipe_data or not recipe_data['quality_options']:
        await interaction.response.edit_message(content="❌ Error: No se encontraron opciones de calidad para este nivel.", view=None)
        return

    # 2. Construir las Opciones de Calidad (Común, Poco Común, Rara)
    quality_options = [
        SelectOption(label=q['quality_name'], value=q['quality_name']) 
        for q in recipe_data['quality_options']
    ]
    
    # 3. Crear el Select Menu (Paso 5: Calidad)
    select_quality = discord.ui.Select(
        custom_id=f"{recipe_id}|{level_name}", # Usamos este custom_id para guardar el contexto
        placeholder=f"Selecciona la Calidad...",
        options=quality_options,
        min_values=1,
        max_values=1,
        row=0
    )
    
    # 4. Asignar el manejador de eventos (Paso 6: Formulario de Cantidad)
    view = discord.ui.View(timeout=180)
    view.add_item(select_quality)
    
    # Conectamos al formulario final (que aún no hemos programado el callback)
    select_quality.callback = final_quality_select_callback 
    
    # 5. Actualizar el mensaje
    await interaction.response.edit_message(
        content=f"**⚙️ Nuevo Pedido:**\n**Paso 5:** Selecciona la Calidad deseada:", 
        view=view
    )

async def inventory_stock_autocomplete(interaction: discord.Interaction, current: str):
    # Ejecuta la búsqueda de ítems en STOCK (inventario_col)
    item_names = await bot.loop.run_in_executor(
        None,
        partial(get_inventory_stock_names, current)
    )
    
    return [
        app_commands.Choice(name=name, value=name)
        for name in item_names
    ]

# Función para obtener los datos finales de la receta
def get_final_recipe_data(context_string, quality):
    # Asumimos que el contexto es "recipe_id|level_name"
    recipe_id, level_name = context_string.split('|')
    
    # Buscamos la receta completa para obtener el nombre y validar
    full_recipe = items_col.find_one({"recipe_id": recipe_id})
    if not full_recipe: return None
    
    # Buscamos el objeto 'variation' específico
    for var in full_recipe.get('variations', []):
        if var.get('level_name') == level_name:
            # Retornamos los datos necesarios para el Modal y la BD
            return {
                "recipe_id": recipe_id,
                "name": full_recipe.get('name', 'N/A'),
                "level_name": level_name,
                "quality": quality,
                "profession": full_recipe.get('profession', 'N/A'), # Oficio que lo crea
            }
    return None

# Función para insertar el pedido en la BD
def insert_pedido(doc):
    # La colección 'pedido' se crea automáticamente si no existe.
    pedidos_col.insert_one(doc)
    return True

def get_user_orders(user_id):
    """Obtiene todos los pedidos realizados por un usuario específico."""
    try:
        # Busca todos los pedidos donde el solicitante_id coincide con el ID de Discord
        orders = pedidos_col.find({
            "solicitante_id": str(user_id)
        }).sort("fecha_solicitud", -1).limit(10) # Ordenar por fecha descendente, solo mostrar los 10 más recientes
        return list(orders)
    except Exception as e:
        print(f"ERROR DE MONGO (get_user_orders): {e}") 
        return []

def get_managed_orders(query_type, identifier):
    """
    Obtiene pedidos según el rol:
    - Si query_type='profession': todos los PENDIENTES de ese oficio (para Maestros).
    - Si query_type='worker_id': todos los ASIGNADOS a ese ID (para Subditos).
    """
    try:
        if query_type == 'profession':
            # Maestros: ver todos los pedidos PENDIENTES de su oficio
            query = {
                "estatus": {"$ne": "ENTREGADA"}, # El operador $ne significa "no igual a"
                "oficio_requerido": identifier
            }
        elif query_type == 'worker_id':
            # Subditos: ver todos los pedidos ASIGNADOS a ellos
            query = {
                "asignado_a_id": str(identifier),
                # Estatus: Ver asignados que aún no estén COMPLETED o CANCELADO
                "estatus": {"$in": ["LISTO PARA RECOGER", "ASIGNADA"]} 
            }
        else:
            return []

        orders = pedidos_col.find(query).sort("fecha_solicitud", -1).limit(20)
        return list(orders)
        
    except Exception as e:
        print(f"ERROR DE MONGO (get_managed_orders): {e}")
        return []

def check_item_exists(name):
    """Verifica si un ítem existe en la colección maestra de recetas."""
    try:
        # Busca el documento, proyectando solo el _id para eficiencia.
        return items_col.find_one({"name": name}, {"_id": 1}) is not None
    except Exception as e:
        print(f"ERROR DE MONGO (check_item_exists): {e}")
        return False

# Función para autocompletar la lista de artesanos disponibles
async def artisan_autocomplete(interaction: discord.Interaction, current: str):
    # 1. Obtener el oficio del Maestro que ejecuta el comando
    maestro_profession = None
    for role in interaction.user.roles:
        if role.name in MANAGEMENT_ROLES:
            maestro_profession = get_profession_from_role(role.name)
            break
            
    if not maestro_profession:
        return [] # Si el maestro no tiene un rol válido, no ofrecemos sugerencias

    # 2. Determinar el rol de Subdito esperado
    # Ej: Si Maestro es Sastrería, el Subdito es 'Sastre'
    subdito_role_name = next((key for key, value in {'Sastre': 'Sastrería', 'Peletero': 'Peletería', 'Herrero': 'Herrería', 'Alquimista': 'Alquimia', 'Cocinero': 'Cocina'}.items() if value == maestro_profession), None)
    
    if not subdito_role_name:
        return []

    # 3. Filtrar los miembros del servidor
    available_members = []
    
    # Itera sobre todos los miembros del servidor (Discord no lo hace automáticamente, debemos iterar)
    for member in interaction.guild.members:
        # Verifica si el miembro tiene el rol de Subdito Y su nombre/apodo coincide con la entrada actual
        member_has_role = discord.utils.get(member.roles, name=subdito_role_name)
        
        if member_has_role and current.lower() in member.display_name.lower():
            available_members.append(app_commands.Choice(name=member.display_name, value=str(member.id)))
            
    # Discord solo permite un máximo de 25 opciones de autocompletado
    return available_members[:25]

def get_full_inventory():
    """Obtiene todos los ítems y cantidades de la colección 'inventario' ordenados alfabéticamente."""
    try:
        # Usamos .sort("name", 1) para ordenar por el campo 'name' en orden ascendente (alfabético)
        inventory = inventario_col.find({}).sort("name", 1) 
        return list(inventory)
    except Exception as e:
        print(f"ERROR DE MONGO (get_full_inventory): {e}")
        return []

# ==============================================================================
# SECCIÓN 5: EVENTOS DE DISCORD
# ==============================================================================

@bot.event
async def on_ready():
    print(f'🤖 Bot: {bot.user} está conectado a Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"🛠️ Sincronizados {len(synced)} comandos.")
    except Exception as e:
        print(f"Error al sincronizar comandos: {e}")

# ==============================================================================
# SECCIÓN 6: CALLBACKS DE INTERACCIÓN (MANEJO DE MENÚS DESPLEGABLES)
# ==============================================================================

# Función que se ejecutará cuando el usuario seleccione un Tipo (Paso 3)
async def type_select_callback(interaction: discord.Interaction):
    selected_type = interaction.data['values'][0]
    selected_category = interaction.data['custom_id'].split('_')[-1]
    
    # 1. Obtener todos los ítems (recetas) que coinciden con la Categoría y Tipo
    def get_recipe_names(cat, item_type):
        try:
            # Proyectamos solo el nombre y el recipe_id
            recipes = items_col.find(
                {"category": cat, "type": item_type},
                {"name": 1, "recipe_id": 1, "_id": 0} 
            )
            return list(recipes)
        except Exception as e:
            print(f"ERROR DE MONGO (get_recipe_names): {e}") 
            return []

    recipe_list = await bot.loop.run_in_executor(
        None,
        partial(get_recipe_names, selected_category, selected_type)
    )

    if not recipe_list:
        await interaction.response.edit_message(content=f"❌ Error: No se encontraron nombres de ítems para '{selected_type}'.", view=None)
        return
    
    # 2. Construir las Opciones del Menú (solo Nombre del Ítem)
    item_name_options = [
        SelectOption(label=recipe['name'][:100], value=recipe['recipe_id']) 
        for recipe in recipe_list
    ]

    # 3. Crear el Select Menu (Paso 3: Nombre del Ítem)
    select_item_name = discord.ui.Select(
        custom_id="select_item_name",
        placeholder="Selecciona el Nombre del Ítem...",
        options=item_name_options,
        min_values=1,
        max_values=1,
        row=0
    )

    # 4. Asignar el manejador de eventos
    view = discord.ui.View(timeout=180)
    view.add_item(select_item_name)
    select_item_name.callback = item_name_select_callback 

    # 5. Actualizar el mensaje
    await interaction.response.edit_message(
        content=f"**⚙️ Nuevo Pedido:**\n**Paso 3:** Selecciona el Nombre del Ítem:", 
        view=view
    )

# Función que se ejecuta cuando el usuario selecciona una categoría
async def category_select_callback(interaction: discord.Interaction):
    
    selected_category = interaction.data['values'][0]

    # 1. Obtener todos los 'tipos' únicos de ese 'category' usando un Thread
    # Usamos partial para pasar el argumento de categoría a la función síncrona
    types = await bot.loop.run_in_executor(
        None,
        partial(get_unique_types, selected_category)
    )
    
    if not types:
        await interaction.response.edit_message(content=f"❌ Error: No se encontraron Tipos (Placas/Tela) para la categoría '{selected_category}'. Verifica tus datos en MongoDB.", view=None)
        return

    # 2. Crear opciones para el Select Menu de Tipo
    type_options = [
        SelectOption(label=t, value=t) for t in types
    ]

    # 3. Crear y Conectar el Select Menu de Tipo
    select_type = discord.ui.Select(
        custom_id=f"select_type_{selected_category}",
        placeholder=f"Selecciona el Tipo de {selected_category}...",
        options=type_options,
        min_values=1,
        max_values=1,
        row=0
    )

    # 4. Preparar la vista
    view = discord.ui.View(timeout=180)
    view.add_item(select_type)
    select_type.callback = type_select_callback 

    # 5. Actualizar el mensaje original
    await interaction.response.edit_message(
        content=f"**⚙️ Nuevo Pedido:**\n**Paso 2:** Selecciona el Tipo de Material/Ítem para **{selected_category}**:", 
        view=view
    )

# Función que se ejecuta cuando el usuario selecciona la Calidad (Paso 5)
async def final_quality_select_callback(interaction: discord.Interaction):
    
    selected_quality = interaction.data['values'][0]
    
    # El custom_id contiene el contexto de la receta: "recipe_id|level_name"
    recipe_context = interaction.data['custom_id']
    
    # 1. Obtener los datos necesarios para abrir el Modal
    final_data = await bot.loop.run_in_executor(
        None,
        partial(get_final_recipe_data, recipe_context, selected_quality)
    )

    if not final_data:
        await interaction.response.edit_message(content="❌ Error: No se encontraron los detalles de la receta. Contacta al administrador.", view=None)
        return

    # 2. Mostrar el formulario Modal (Paso 6: Cantidad y Envío)
    await interaction.response.send_modal(OrderModal(final_data))

class OrderModal(discord.ui.Modal, title='Detalles Finales del Pedido'):
    
    # El diccionario recipe_data contiene toda la información de contexto necesaria
    def __init__(self, recipe_data, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.recipe_data = recipe_data
        
        # Etiqueta dinámica para informar al usuario sobre qué calidad eligió
        quality_label = f"Calidad: {recipe_data['quality']} | Cantidad"
        
        # Campo Cantidad
        self.quantity = discord.ui.TextInput(
            label=quality_label, # Muestra la calidad seleccionada
            placeholder="Ingresa la cantidad de ítems (ej: 1)",
            min_length=1,
            max_length=3,
            required=True
        )
        self.add_item(self.quantity) # Añadir el campo de cantidad al modal

    async def on_submit(self, interaction: discord.Interaction):
        # 1. Obtener valores y hacer una validación básica
        req_quantity_str = self.quantity.value
        
        if not req_quantity_str.isdigit():
             await interaction.response.send_message("❌ Error: La cantidad debe ser un número válido.", ephemeral=True)
             return
        
        req_quantity = int(req_quantity_str)
        
        # 2. Construir el Documento 'pedido'
        pedido_doc = {
            "item_name": self.recipe_data['name'],
            "recipe_id": self.recipe_data['recipe_id'],
            "level": self.recipe_data['level_name'],
            "quality": self.recipe_data['quality'],
            "cantidad": req_quantity,
            "oficio_requerido": self.recipe_data['profession'],
            "solicitante_id": str(interaction.user.id),
            "estatus": "PENDIENTE",
            "fecha_solicitud": discord.utils.utcnow()
        }
        
        # 3. Insertar en MongoDB (Se ejecuta en un thread para no bloquear)
        try:
            await bot.loop.run_in_executor(None, partial(insert_pedido, pedido_doc))
        except Exception as e:
            print(f"ERROR AL INSERTAR PEDIDO: {e}")
            await interaction.response.send_message("❌ Error crítico al guardar el pedido en la base de datos.", ephemeral=True)
            return

        # 4. Respuesta final (Pública para que los artesanos vean el pedido)
        await interaction.response.send_message(
            f"✅ **¡NUEVO PEDIDO CREADO!**\n"
            f"**Artículo:** {pedido_doc['item_name']} - Nivel {pedido_doc['level']} ({pedido_doc['quality']})\n"
            f"**Cantidad:** {pedido_doc['cantidad']}\n"
            f"**Oficio:** {pedido_doc['oficio_requerido']}\n"
            f"Solicitado por: {interaction.user.mention}",
            ephemeral=False
        )
    
# ==============================================================================
# SECCIÓN 7: COMANDOS DE BARRA DIAGONAL (SLASH COMMANDS)
# ==============================================================================

# --- CONFIGURACIÓN DE ROLES DE GESTIÓN ---
MANAGEMENT_ROLES = [
    "Sastre Maestro", "Peletero Maestro", "Herrero Maestro", "Alquimista Maestro", "Cocinero Maestro", "Joyero Maestro",
    "Sastre", "Peletero", "Herrero", "Alquimista", "Cocinero", "Joyero"
]
PROFESSION_MAPPING = {
    "Sastre": "Sastrería",
    "Peletero": "Peletería",
    "Herrero": "Herrería",
    "Alquimista": "Alquimia",
    "Cocinero": "Cocina",
    "Joyero" : "Joyería"
}
# Define los Roles que serán Jefes de Oficio (¡AJUSTA ESTOS NOMBRES!)
CHIEF_ROLES = ["Sastre Maestro", "Herrero Maestro", "Peletero Maestro", "Alquimista Maestro", "Cocinero Maestro", "Joyero Maestro"] 

def get_profession_from_role(role_name):
    """
    Simplifica el nombre del rol quitando ' Maestro' para obtener la profesión base
    y lo mapea al nombre completo de la profesión de la BD (Sastrería, Peletería, etc.).
    """
    # 1. Obtener el nombre base
    profession_base_name = role_name.replace(" Maestro", "").strip() 
    return PROFESSION_MAPPING.get(profession_base_name, profession_base_name)

# --- COMANDO /VerPedidos ---
@bot.tree.command(name="verpedidos", description="Muestra pedidos pendientes (Maestro) o asignados (Subdito).")
@app_commands.checks.has_any_role(*MANAGEMENT_ROLES)
async def view_orders_command(interaction: discord.Interaction):
    
    is_maestro = False
    chief_profession = None
    
    # 1. Determinar el rol y el oficio base
    for role in interaction.user.roles:
        if role.name in MANAGEMENT_ROLES:
            # Es un Maestro si el nombre del rol contiene " Maestro"
            if " Maestro" in role.name:
                is_maestro = True
            
            chief_profession = get_profession_from_role(role.name)
            if chief_profession:
                break

    if not chief_profession:
        await interaction.response.send_message("❌ Error: No se pudo determinar tu oficio base (Sastrería, Herrería, etc.) a partir de tu rol.", ephemeral=True)
        return

    # 2. Definir la consulta a MongoDB
    if is_maestro:
        # MAESTRO: Ver todos los pedidos pendientes de su profesión
        query_type = 'profession'
        identifier = chief_profession
        list_title = f"👑 Pedidos PENDIENTES de {chief_profession}"
        no_orders_msg = f"✅ ¡No hay pedidos pendientes para el oficio **{chief_profession}**!"
    else:
        # SUBDITO/TRABAJADOR: Ver pedidos asignados a su ID
        query_type = 'worker_id'
        identifier = interaction.user.id
        list_title = f"✍️ Pedidos ASIGNADOS a ti ({chief_profession})"
        no_orders_msg = "✅ ¡No tienes pedidos asignados en este momento!"


    # 3. Consultar pedidos en segundo plano
    managed_orders = await bot.loop.run_in_executor(
        None,
        partial(get_managed_orders, query_type, identifier)
    )

    if not managed_orders:
        await interaction.response.send_message(no_orders_msg, ephemeral=True)
        return

    # 4. Formatear y Mostrar Resultados
    embed = discord.Embed(
        title=list_title,
        color=discord.Color.gold() if is_maestro else discord.Color.teal()
    )
    
    for order in managed_orders:
        order_id_visible = str(order['_id']) # ID completo de 24 caracteres
        solicitante_mention = f"<@{order['solicitante_id']}>"
        
        # Muestra el artesano asignado
        asignado_a_text = f"Asignado a: <@{order.get('asignado_a_id')}>" if order.get('asignado_a_id') else "**SIN ASIGNAR**"
        
        # El estatus
        current_status = order.get('estatus', 'N/A')

        # 🟢 CORRECCIÓN EN EL VALOR DEL CAMPO
        field_value = (
            f"**Nombre:** {order['item_name']}\n"
            f"**Cantidad:** {order['cantidad']} | **Nivel:** {order[ 'level']} ({order['quality']})\n" 
            f"**Solicitado por:** {solicitante_mention}\n"
        )
        
        # Lógica de Maestro/Subdito para el valor del campo
        if is_maestro:
             field_value += f"**Estatus:** **{current_status}** | {asignado_a_text}"
        else:
             field_value += f"**Estatus:** **{current_status}**"
        
        # 1. Añadir el campo del Pedido (Título solo con ID)
        embed.add_field(
            name=f"ID: {order_id_visible}", # <-- Título más limpio
            value=field_value,
            inline=False
        )
        
    await interaction.response.send_message(embed=embed, ephemeral=True) 
# Manejo de error de roles para /VerPedidos
@view_orders_command.error
async def view_orders_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingAnyRole):
        await interaction.response.send_message("🔒 No tienes el rol de Jefe de Oficio para usar este comando.", ephemeral=True)

# --- /Ping ---
@bot.tree.command(name="ping", description="Responde con Ping y verifica la BD.")
async def ping_command(interaction: discord.Interaction):
    try:
        # Usamos count_documents como prueba de conexión ligera
        usuarios_col.count_documents({}) 
        db_status = "✅ BD Conectada y funcionando."
    except Exception:
        db_status = "❌ BD Desconectada o error de consulta."
        
    await interaction.response.send_message(f"Pong! {db_status}", ephemeral=True)

# --- /CrearPedido ---
@bot.tree.command(name="crearpedido", description="Inicia el proceso de creación de un pedido de crafteo.")
async def create_order_command(interaction: discord.Interaction):
    
    # 1. Obtener las categorías desde la BD usando un Thread (para no bloquear Discord)
    # Ejecutamos la función síncrona en un hilo de segundo plano
    categories = await bot.loop.run_in_executor(
        None, 
        get_unique_categories # Pasamos el nombre de la función SÍNCRONA
    )
    
    if not categories:
        await interaction.response.send_message("❌ Error: No se encontraron categorías de crafteo en la base de datos o hubo un fallo de conexión.", ephemeral=True)
        return
    
    # 2. Crear las opciones para el Select Menu
    category_options = [
        SelectOption(label=cat, value=cat) for cat in categories
    ]

    # 3. Crear el Select Menu (Primer filtro: Categoría)
    select_category = discord.ui.Select(
        custom_id="select_category",
        placeholder="Selecciona la Categoría (Armadura, Arma...)",
        options=category_options,
        min_values=1,
        max_values=1,
        row=0
    )

    # 4. Asignar el manejador de eventos y Vista
    view = discord.ui.View(timeout=180) 
    view.add_item(select_category)
    select_category.callback = category_select_callback 
    
    # 5. Enviar el mensaje inicial
    await interaction.response.send_message(
        "**⚙️ Nuevo Pedido:**\n**Paso 1:** Selecciona la categoría del artículo:", 
        view=view, 
        ephemeral=True 
    )

# --- /MisPedidos ---
@bot.tree.command(name="mispedidos", description="Muestra el estado de los pedidos que has solicitado.")
async def my_orders_command(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    # 1. Consultar pedidos del usuario en segundo plano
    user_orders = await bot.loop.run_in_executor(
        None,
        partial(get_user_orders, user_id)
    )
    
    if not user_orders:
        await interaction.response.send_message("✅ ¡No has solicitado ningún pedido aún!", ephemeral=True)
        return

    # 2. Formatear y Mostrar Resultados
    embed = discord.Embed(
        title=f"📋 Estado de tus Pedidos Recientes",
        color=discord.Color.green()
    )
    
    status_emoji = {
        "PENDIENTE": "🕒",
        "ASIGNADO": "✍️",
        "COMPLETADO": "✅",
        "CANCELADO": "❌"
    }
    
    for order in user_orders:
        status = order.get('estatus', 'N/A')
        emoji = status_emoji.get(status, '❓')
        
        # Usamos los últimos 5 caracteres del ObjectId como ID visible
        order_id_visible = str(order['_id'])

        # Mostrar el nombre del artesano si está asignado
        asignado_a = order.get('asignado_a_id')
        
        # Discord usa <@ID_DE_USUARIO> para mencionar a alguien
        asignado_text = f"**Artesano:** <@{asignado_a}>" if asignado_a else "**Artesano:** Pendiente"

        embed.add_field(
            name=f"{emoji} ID {order_id_visible} | {order['item_name']} ({order['quality']})",
            value=(
                f"**Cantidad:** {order['cantidad']} | **Nivel:** {order['level']}\n"
                f"{asignado_text} | **Estatus:** **{status}**"
            ),
            inline=False
        )
        
    await interaction.response.send_message(embed=embed, ephemeral=True) # ephemeral=True: Solo el usuario ve sus pedidos

# --- /AsignarPedido ---
@bot.tree.command(name="asignarpedido", description="Asigna un pedido a un artesano y cambia el estado.")
@app_commands.checks.has_any_role(*MANAGEMENT_ROLES) 
@app_commands.autocomplete(artesano=artisan_autocomplete)
@app_commands.describe(
    pedido_id="El ID completo (24 caracteres) del pedido a asignar.",
    artesano="El miembro de Discord que crafteará el ítem."
)
async def assign_order_command(interaction: discord.Interaction, pedido_id: str, artesano: str):
    pedido_id = pedido_id.strip()
    
    # 1. Obtener el objeto Member a partir del ID (string)
    member_to_assign = interaction.guild.get_member(int(artesano))
    
    if not member_to_assign:
        await interaction.response.send_message("❌ Error: No se pudo encontrar el miembro con el ID proporcionado.", ephemeral=True)
        return

    # 2. Obtener el Oficio del Maestro y del Artesano a asignar
    maestro_profession = None
    for role in interaction.user.roles:
        if role.name in MANAGEMENT_ROLES:
            maestro_profession = get_profession_from_role(role.name)
            break
            
    artesano_profession = get_profession_from_role(next((r.name for r in member_to_assign.roles if r.name in MANAGEMENT_ROLES), None))

    # Validación 1: El Maestro debe tener un oficio válido
    if not maestro_profession:
        await interaction.response.send_message("❌ Error: No se pudo determinar tu oficio para asignar pedidos.", ephemeral=True)
        return

    # Validación 2: El artesano DEBE tener el mismo rol de oficio
    if artesano_profession != maestro_profession:
        await interaction.response.send_message(f"🔒 Error: Solo puedes asignar pedidos a artesanos que tengan el rol de **{maestro_profession.replace('ería', '')}**.", ephemeral=True)
        return
        
    # --- FUNCIÓN ANIDADA PARA MONGO DB ---
    def update_assignment():
        from bson.objectid import ObjectId
        
        # Intentamos encontrar el pedido usando el ObjectId COMPLETO.
        try:
            order_doc = pedidos_col.find_one({
                "_id": ObjectId(pedido_id), # <- Funciona con el ID de 24 caracteres
                "oficio_requerido": maestro_profession
            })
        except Exception:
            return "INVALID_ID" # Captura el error InvalidId si el ID es muy corto
            
        if not order_doc:
            return "NOT_FOUND"

        # Actualizamos el documento
        pedidos_col.update_one(
            {"_id": ObjectId(pedido_id)},
            {"$set": {
                "estatus": "ASIGNADA",
                "asignado_a_id": str(member_to_assign.id)
            }}
        )
        return order_doc['item_name']

    # 3. Ejecutar la actualización en segundo plano
    result_name = await bot.loop.run_in_executor(None, update_assignment)

    if result_name == "INVALID_ID":
        await interaction.response.send_message("❌ Error: El ID del pedido no tiene el formato correcto (debe ser el ID completo de 24 caracteres).", ephemeral=True)
        return

    if result_name == "NOT_FOUND":
        await interaction.response.send_message(f"❌ Error: Pedido #{pedido_id} no encontrado o no pertenece a tu oficio ({maestro_profession}).", ephemeral=True)
        return

    # 4. Respuesta final (Pública)
    await interaction.response.send_message(
        f"✅ Pedido #{pedido_id} **ASIGNADO** a {member_to_assign.mention} ({maestro_profession}).\n"
        f"El estado del ítem **{result_name}** ha cambiado a **ASIGNADA**.",
        ephemeral=False
    )
    
    # 5. ¡ENVIAR NOTIFICACIÓN POR DM AL ARTESANO ASIGNADO!
    try:
        # Obtenemos el objeto usuario (Member) que ya lo tenemos como member_to_assign
        
        await member_to_assign.send(
            f"🛠️ **¡NUEVA TAREA ASIGNADA!** 🛠️\n\n"
            f"El Maestro {interaction.user.display_name} te ha asignado un nuevo pedido:\n"
            f"**Artículo:** {result_name}\n"
            f"**ID de Pedido:** {pedido_id}\n"
            f"Usa el comando **/verpedidos** para ver tu lista de tareas y **/completar** cuando hayas terminado."
        )
    except Exception as e:
        print(f"Error al enviar DM de asignación al artesano {member_to_assign.id}: {e}")
        # Notificamos al Maestro en privado si el DM falla
        await interaction.followup.send(f"⚠️ Advertencia: No pude enviar el DM de notificación a {member_to_assign.display_name}.", ephemeral=True)

# --- /RecogerPedido ---
@bot.tree.command(name="recogerpedido", description="Marca tu pedido como Entregado, confirmando la recepción del ítem.")
@app_commands.describe(pedido_id="El ID corto (primeros 8 caracteres) del pedido que deseas marcar como Entregado.")
async def pickup_order_command(interaction: discord.Interaction, pedido_id: str):
    pedido_id = pedido_id.strip()    
    user_id_str = str(interaction.user.id)
    
    def update_status():
        from bson.objectid import ObjectId
        
        # Buscamos el pedido, verificando que el usuario sea el solicitante y que el estado sea 'LISTO PARA RECOGER'
        order_doc = pedidos_col.find_one({
            "_id": ObjectId(pedido_id),
            "solicitante_id": user_id_str,
            "estatus": "LISTO PARA RECOGER"
        })
        
        if not order_doc:
            return "NOT_FOUND"

        # Actualizamos el estado a ENTREGADA
        pedidos_col.update_one(
            {"_id": ObjectId(pedido_id)},
            {"$set": {"estatus": "ENTREGADA"}}
        )
        return order_doc['item_name']

    result_name = await bot.loop.run_in_executor(None, update_status)

    if result_name == "NOT_FOUND":
        await interaction.response.send_message(
            f"❌ Error: Pedido #{pedido_id} no encontrado, no eres el solicitante, o aún no está **LISTO PARA RECOGER**.",
            ephemeral=True
        )
        return
        
    # Respuesta final
    await interaction.response.send_message(
        f"🎉 ¡Tu pedido #{pedido_id} del ítem **{result_name}** ha sido marcado como **ENTREGADA**!\n"
        f"Gracias por tu compra.",
        ephemeral=False
    )

# --- /CompletarPedido ---
@bot.tree.command(name="completarpedido", description="Marca un pedido como LISTO PARA RECOGER.")
@app_commands.checks.has_any_role(*MANAGEMENT_ROLES)
@app_commands.describe(
    pedido_id="El ID completo (24 caracteres) del pedido que has terminado."
)
async def complete_order_command(interaction: discord.Interaction, pedido_id: str):
    pedido_id = pedido_id.strip()    
    user_id_str = str(interaction.user.id)
    user_roles = [r.name for r in interaction.user.roles]
    # Determinar si el usuario tiene un rol de Maestro
    is_maestro = any("Maestro" in role for role in user_roles) 
    
    worker_profession = None
    
    # 1. Obtener el Oficio del usuario que ejecuta el comando (Para validación)
    for role in interaction.user.roles:
        if role.name in MANAGEMENT_ROLES:
            worker_profession = get_profession_from_role(role.name)
            break
            
    if not worker_profession:
        await interaction.response.send_message("❌ Error: No se pudo determinar tu oficio para completar pedidos.", ephemeral=True)
        return
        
    # 2. Función síncrona para actualizar el estado
    def update_status_to_ready():
        from bson.objectid import ObjectId
        
        # 2a. DEFINICIÓN DE LA QUERY DE BÚSQUEDA BASE
        query = {
            "estatus": {"$ne": "ENTREGADA"},        
            "oficio_requerido": worker_profession    # El pedido debe ser del oficio del usuario
        }
        
        # Intentamos obtener el ObjectId. Si falla, el try/except lo captura
        try:
            query["_id"] = ObjectId(pedido_id)
        except Exception:
            return "INVALID_ID"

        # 2b. REGLA DE ACCESO: SOLO MAESTRO O ASIGNADO PUEDEN COMPLETAR
        
        # Si el usuario NO es maestro (es Subdito o trabajador):
        if not is_maestro:
            # 1. El pedido DEBE estar asignado a este usuario
            query["asignado_a_id"] = user_id_str
            # 2. El Subdito NO puede completar su propio pedido (aunque se lo asigne un maestro)
            query["solicitante_id"] = {"$ne": user_id_str} 
            
        # Si es MAESTRO, la query ya está lista (solo necesita _id y oficio_requerido)
            
        try:
            order_doc = pedidos_col.find_one(query)
        except Exception as e:
            print(f"Error de búsqueda en MongoDB (complete): {e}")
            return "NOT_FOUND" 

        if not order_doc:
            return "NOT_FOUND" # El pedido no cumple las reglas de acceso (no es Maestro ni asignado)

        # 2c. Actualizamos el estado
        pedidos_col.update_one(
            {"_id": ObjectId(pedido_id)},
            {"$set": {"estatus": "LISTO PARA RECOGER"}}
        )
        return order_doc['item_name']

    # 3. Ejecutar la actualización en segundo plano
    result_name = await bot.loop.run_in_executor(None, update_status_to_ready)

    # 4. Manejo de resultados (Mantenemos igual)
    if result_name == "INVALID_ID":
        await interaction.response.send_message("❌ Error: El ID del pedido no tiene el formato correcto (24 caracteres).", ephemeral=True)
        return
    if result_name == "NOT_FOUND":
        await interaction.response.send_message(
            f"❌ Error: El pedido #{pedido_id} no fue encontrado o no está asignado a ti/tu oficio.", 
            ephemeral=True
        )
        return
        
    # 5. Respuesta final (Pública y Envío de DM)
    
    # Obtenemos el documento completo para obtener el ID del solicitante
    # Nota: order_doc ya tiene el ID si no devolvió NOT_FOUND/INVALID_ID
    final_order = pedidos_col.find_one({"_id": ObjectId(pedido_id)})
    solicitante_id = final_order['solicitante_id']
    
    # 5a. Enviamos el mensaje público al canal de pedidos
    await interaction.response.send_message(
        f"✅ ¡PEDIDO COMPLETADO! **{result_name}** ha sido marcado como **LISTO PARA RECOGER**.\n"
        f"El solicitante (<@{solicitante_id}>) puede usar el comando **/recoger** para finalizar.",
        ephemeral=False
    )

    # 5b. ¡ENVIAR NOTIFICACIÓN POR DM!
    try:
        # 1. Obtenemos el objeto usuario a partir de su ID
        solicitante = bot.get_user(int(solicitante_id))
        
        if solicitante:
            # 2. Le enviamos un DM (Mensaje Directo)
            await solicitante.send(
                f"🎉 ¡Tu pedido está listo para recoger!\n\n"
                f"El ítem **{result_name}** (Pedido ID: **{pedido_id}**) ha sido completado por el artesano.\n"
                f"Usa el comando **/recoger pedido_id: {pedido_id}** en el servidor de Discord para marcarlo como **ENTREGADA**."
            )
        else:
            # Esto puede pasar si el usuario ya no está en el servidor
            print(f"Advertencia: No se pudo encontrar al solicitante con ID {solicitante_id} para enviar DM.")
            
    except Exception as e:
        print(f"Error al enviar DM al solicitante {solicitante_id}: {e}")
        # La interacción ya fue respondida, así que solo registramos el error

# --- /InventarioAgregar ---
@bot.tree.command(name="inventarioagregar", description="Agrega nuevos ítems o aumenta la cantidad de un ítem existente.")
@app_commands.describe(
    item_name="Nombre del ítem (solo se muestran ítems con stock).",
    cantidad="Cantidad a agregar (número entero positivo)."
)
@app_commands.autocomplete(item_name=inventory_stock_autocomplete)
@app_commands.checks.has_any_role(*MANAGEMENT_ROLES)
async def add_inventory_command(interaction: discord.Interaction, item_name: str, cantidad: int):
    
    await interaction.response.defer(ephemeral=True)
    item_name_stripped = item_name.strip()

    # 1. Validación de Cantidad (Ya existía)
    if cantidad <= 0:
        await interaction.followup.send("❌ Error: La cantidad debe ser mayor a cero para agregar.", ephemeral=True)
        return

    # 2. 🟢 AGREGAR LA LÓGICA DE ACTUALIZACIÓN Y CONFIRMACIÓN (CORRECCIÓN VITAL)

    # Ejecutar la actualización en un hilo de fondo con cantidad positiva
    result = await bot.loop.run_in_executor(
        None,
        partial(update_inventory, item_name_stripped, cantidad) 
    )

    if result == "ERROR":
        await interaction.followup.send("❌ Error: Fallo al actualizar el inventario.", ephemeral=True)
        return
        
    # Buscamos el documento actualizado para obtener la cantidad total
    final_doc = inventario_col.find_one({"name": item_name_stripped})
    final_quantity = final_doc.get("quantity", 0)

    await interaction.followup.send(
        f"✅ Inventario Actualizado:\n"
        f"Se agregaron **{cantidad}** de **{item_name_stripped}**.\n"
        f"Cantidad total en stock: **{final_quantity}**.",
        ephemeral=False
    )

# --- /InventarioRetirar ---
@bot.tree.command(name="inventarioretirar", description="Retira una cantidad de un ítem existente del inventario.")
@app_commands.describe(
    item_name="Nombre del ítem (se autocompleta si existe).",
    cantidad="Cantidad a retirar (debe ser un número entero positivo)."
)
@app_commands.autocomplete(item_name=inventory_stock_autocomplete)
@app_commands.checks.has_any_role(*MANAGEMENT_ROLES)
async def remove_inventory_command(interaction: discord.Interaction, item_name: str, cantidad: int):
    
    await interaction.response.defer(ephemeral=True)
    
    # Validacion simple de cantidad
    if cantidad <= 0:
        await interaction.followup.send("❌ Error: La cantidad a retirar debe ser mayor a cero.", ephemeral=True)
        return
        
    item_name_stripped = item_name.strip()
    
    # Ejecutar la actualización en un hilo de fondo con cantidad negativa
    # Nota: Si el resultado es "DELETED", la cantidad fue <= 0
    result = await bot.loop.run_in_executor(
        None,
        partial(update_inventory, item_name_stripped, -cantidad) # CANTIDAD NEGATIVA
    )

    if result == "ERROR":
        await interaction.followup.send("❌ Error: Fallo al actualizar el inventario.", ephemeral=True)
        return
    
    if result == "DELETED":
        await interaction.followup.send(
            f"✅ Inventario Actualizado:\n"
            f"Se retiraron **{cantidad}** de **{item_name_stripped}**.\n"
            f"El ítem fue **eliminado** del inventario por tener 0 o menos unidades.",
            ephemeral=False
        )
        return

    # Si no fue eliminado, confirmar la cantidad final
    final_doc = inventario_col.find_one({"name": item_name_stripped})
    final_quantity = final_doc.get("quantity", 0)

    await interaction.followup.send(
        f"✅ Inventario Actualizado:\n"
        f"Se retiraron **{cantidad}** de **{item_name_stripped}**.\n"
        f"Cantidad restante: **{final_quantity}**.",
        ephemeral=False
    )

# --- /InventarioVer ---
@bot.tree.command(name="inventariover", description="Muestra la lista completa de ítems en el inventario y sus cantidades.")
@app_commands.checks.has_any_role(*MANAGEMENT_ROLES)
async def view_inventory_command(interaction: discord.Interaction):
    
    await interaction.response.defer(ephemeral=True) # DEFERIR RESPUESTA
    
    # 1. Consultar inventario ordenado en segundo plano
    inventory_list = await bot.loop.run_in_executor(
        None,
        get_full_inventory
    )
    
    if not inventory_list:
        await interaction.followup.send("✅ El inventario está actualmente vacío.", ephemeral=True)
        return

    # 2. Formatear los resultados
    inventory_text = []
    
    for item in inventory_list:
        name = item.get('name', 'Ítem Desconocido')
        quantity = item.get('quantity', 0)
        
        if quantity > 0:
            inventory_text.append(f"• {name} **{quantity}**")
    
    # Si la lista de texto es demasiado larga para un solo campo (límite de 1024 caracteres), 
    # la dividimos en un solo bloque unido por saltos de línea.
    
    final_output = "\n".join(inventory_text)
    
    embed = discord.Embed(
        title=f"📦 Inventario del Gremio",
        description=final_output, # Mostrar todo el listado en la descripción
        color=discord.Color.blue()
    )
    
    # 3. Respuesta final
    await interaction.followup.send(embed=embed, ephemeral=False)

# Manejo de error de roles para /InventarioVer 
@view_inventory_command.error
async def view_inventory_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingAnyRole):
        await interaction.response.send_message("🔒 No tienes un rol de gestión de oficios para ver el inventario.", ephemeral=True)

# --- 8. INICIAR EL BOT ---
if DISCORD_TOKEN:
    bot.run(DISCORD_TOKEN)
else:
    print("ERROR: El token de Discord no fue encontrado. Revisa el archivo .env.")