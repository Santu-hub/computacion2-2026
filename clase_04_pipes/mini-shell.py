#!/usr/bin/env python3
import os
import sys

def parsear_linea(linea):
    """
    Parsea una línea de comando.
    Retorna (comando, args, archivo_salida, archivo_entrada)
    """
    partes = linea.split()
    if not partes:
        return None, [], None, None
    
    comando = partes[0]
    args = []
    archivo_salida = None
    archivo_entrada = None

    i = 1
    while i < len(partes):
        if partes[i] == ">":
            if i + 1 < len(partes):
                archivo_salida = partes[i + 1]
                i += 2
            else:
                print("Error: falta nombre de archivo para redirección de salida")
                return None, [], None, None
        elif partes[i] == "<":
            if i + 1 < len(partes):
                archivo_entrada = partes[i + 1]
                i += 2
            else:
                print("Error: falta nombre de archivo para redirección de entrada")
                return None, [], None, None
        else:
            args.append(partes[i])
            i += 1

    return comando, args, archivo_salida, archivo_entrada

def ejecutar(comando, args, archivo_salida=None, archivo_entrada=None):
    """Ejecuta un comando con redirección opcional usando fork + exec."""
    pid = os.fork()

    if pid == 0:
        # --- PROCESO HIJO ---
        
        # Redirección de Salida (>)
        if archivo_salida:
            try:
                # O_CREAT: crea si no existe, O_TRUNC: vacía si existe
                fd = os.open(archivo_salida, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
                os.dup2(fd, 1)  # 1 es stdout
                os.close(fd)
            except OSError as e:
                print(f"minish: error al abrir salida: {e}", file=sys.stderr)
                os._exit(1)

        # Redirección de Entrada (<)
        if archivo_entrada:
            try:
                fd = os.open(archivo_entrada, os.O_RDONLY)
                os.dup2(fd, 0)  # 0 es stdin
                os.close(fd)
            except FileNotFoundError:
                print(f"minish: {archivo_entrada}: No existe el archivo", file=sys.stderr)
                os._exit(1)
            except OSError as e:
                print(f"minish: error al abrir entrada: {e}", file=sys.stderr)
                os._exit(1)

        # Ejecución del programa
        try:
            os.execvp(comando, [comando] + args)
        except OSError as e:
            print(f"minish: {comando}: comando no encontrado", file=sys.stderr)
            os._exit(127)

    else:
        # --- PROCESO PADRE ---
        _, status = os.wait()
        return os.WEXITSTATUS(status)

def main():
    while True:
        try:
            # Mostramos el prompt con el directorio actual
            prompt = f"minish:{os.getcwd()}$ "
            linea = input(prompt)
        except EOFError:
            print("\nChau!")
            break

        linea = linea.strip()
        if not linea:
            continue

        comando, args, salida, entrada = parsear_linea(linea)
        
        if not comando:
            continue

        # Comandos internos (Built-ins)
        if comando == "exit":
            break
        
        elif comando == "cd":
            try:
                # Si no hay argumentos, vamos al HOME
                destino = args[0] if args else os.environ.get("HOME", ".")
                os.chdir(destino)
            except (OSError, IndexError) as e:
                print(f"cd: {e}")
            continue

        # Comandos externos
        ejecutar(comando, args, salida, entrada)

if __name__ == "__main__":
    main()