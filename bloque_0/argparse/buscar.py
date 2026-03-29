import argparse
import sys

def procesar_lineas(lineas, patron, args, nombre_origen=None):
    coincidencias = 0
    
    for i, linea in enumerate(lineas, 1):
        linea_limpia = linea.rstrip('\n')
        
        # Lógica de búsqueda (con o sin ignore-case)
        p = patron.lower() if args.ignore_case else patron
        l = linea_limpia.lower() if args.ignore_case else linea_limpia
        
        # Verificamos si el patrón está en la línea (invertido si -v)
        existe = p in l
        if args.invert:
            existe = not existe
            
        if existe:
            coincidencias += 1
            if not args.count:
                # Formateo de salida: [archivo:][linea:]contenido
                salida = ""
                if nombre_origen: salida += f"{nombre_origen}:"
                if args.line_number: salida += f"{i}:"
                print(f"{salida}{linea_limpia}")
                
    return coincidencias

# 1. Configuración de Argparse
parser = argparse.ArgumentParser(description="Versión simplificada de grep.")
parser.add_argument("patron", help="Patrón de texto a buscar")
parser.add_argument("archivos", nargs="*", help="Archivos donde buscar")

parser.add_argument("-i", "--ignore-case", action="store_true", help="Ignorar mayúsculas")
parser.add_argument("-n", "--line-number", action="store_true", help="Mostrar número de línea")
parser.add_argument("-c", "--count", action="store_true", help="Solo mostrar conteo")
parser.add_argument("-v", "--invert", action="store_true", help="Invertir búsqueda")

args = parser.parse_args()

# Forzar números de línea si hay más de un archivo (como el grep real)
if len(args.archivos) > 1:
    args.line_number = True

total_general = 0

# 2. Lógica de entrada (Stdin vs Archivos)
if not args.archivos and not sys.stdin.isatty():
    # Caso: cat log.txt | python3 buscar.py "error"
    total_general = procesar_lineas(sys.stdin, args.patron, args)
else:
    # Caso: python3 buscar.py "error" log.txt
    for nombre in args.archivos:
        try:
            with open(nombre, 'r') as f:
                cant = procesar_lineas(f, args.patron, args, nombre if len(args.archivos) > 1 else None)
                if args.count:
                    print(f"{nombre}: {cant} coincidencias")
                total_general += cant
        except FileNotFoundError:
            print(f"Error: No se pudo leer '{nombre}'", file=sys.stderr)

if args.count and len(args.archivos) > 1:
    print(f"Total: {total_general} coincidencias")