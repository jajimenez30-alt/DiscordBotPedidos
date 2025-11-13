#!/usr/bin/env bash

# Exportar la variable PORT requerida por Render para evitar el timeout
# Esto engaña a Render haciéndole creer que hay un puerto abierto
export PORT=10000 

# Iniciar tu bot de Python
python bot.py
