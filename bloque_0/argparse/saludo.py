import sys

# 1. Verificamos si se pasó el argumento (el largo debe ser al menos 2)
if len(sys.argv) < 2:
    print("Uso: saludo.py <nombre>")
    sys.exit(1)

# 2. Tomamos el primer argumento después del nombre del archivo
nombre = sys.argv[1]

# 3. Imprimimos el saludo
print(f"Hola, {nombre}!")