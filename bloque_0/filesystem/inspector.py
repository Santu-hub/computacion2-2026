import os
import sys
import stat
import pwd
import grp
import argparse
from datetime import datetime

def obtener_tipo(modo, ruta):
    if stat.S_ISLNK(modo):
        return f"enlace simbólico -> {os.readlink(ruta)}"
    elif stat.S_ISDIR(modo):
        return "directorio"
    elif stat.S_ISCHR(modo):
        return "dispositivo de caracteres"
    elif stat.S_ISBLK(modo):
        return "dispositivo de bloques"
    elif stat.S_ISREG(modo):
        return "archivo regular"
    elif stat.S_ISFIFO(modo):
        return "FIFO (pipe)"
    elif stat.S_ISSOCK(modo):
        return "socket"
    return "desconocido"

def formatear_permisos(modo):
    # Convierte el modo a cadena rwxrwxrwx
    chars = "-rwxrwxrwx"
    ret = ""
    # Permisos de dueño, grupo y otros
    bits = [
        stat.S_IRUSR, stat.S_IWUSR, stat.S_IXUSR,
        stat.S_IRGRP, stat.S_IWGRP, stat.S_IXGRP,
        stat.S_IROTH, stat.S_IWOTH, stat.S_IXOTH
    ]
    for i, bit in enumerate(bits):
        if modo & bit:
            ret += chars[i+1]
        else:
            ret += "-"
    # Valor octal (ultimos 3 digitos)
    octal = oct(modo & 0o777)[2:]
    return f"{ret} ({octal})"

def main():
    parser = argparse.ArgumentParser(description="Muestra información detallada de un archivo.")
    parser.add_argument("ruta", help="Ruta del archivo a inspeccionar")
    
    try:
        args = parser.parse_args()
        ruta = args.ruta
        
        # Usamos lstat para no seguir el enlace si es un symlink
        info = os.lstat(ruta)
        modo = info.st_mode
        
        print(f"Archivo: {ruta}")
        print(f"Tipo: {obtener_tipo(modo, ruta)}")
        
        # Tamaño
        tam_bytes = info.st_size
        if tam_bytes > 1024:
            print(f"Tamaño: {tam_bytes} bytes ({tam_bytes/1024:.2f} KB)")
        else:
            print(f"Tamaño: {tam_bytes} bytes")
            
        print(f"Permisos: {formatear_permisos(modo)}")
        
        # Propietario y Grupo
        user = pwd.getpwuid(info.st_uid).pw_name
        group = grp.getgrgid(info.st_gid).gr_name
        print(f"Propietario: {user} (uid: {info.st_uid})")
        print(f"Grupo: {group} (gid: {info.st_gid})")
        
        print(f"Inodo: {info.st_ino}")
        print(f"Enlaces duros: {info.st_nlink}")
        
        # Fechas
        print(f"Última modificación: {datetime.fromtimestamp(info.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Último acceso: {datetime.fromtimestamp(info.st_atime).strftime('%Y-%m-%d %H:%M:%S')}")

        # Contenido si es directorio
        if stat.S_ISDIR(modo):
            elementos = len(os.listdir(ruta))
            print(f"Contenido: {elementos} elementos")

        sys.exit(0)

    except FileNotFoundError:
        print(f"Error: No se encontró el archivo '{ruta}'", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Error: Permiso denegado para acceder a '{ruta}'", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error inesperado: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()