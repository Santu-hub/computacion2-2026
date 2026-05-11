import os

# Crear varios hijos
hijos = []
for i in range(3):
    pid = os.fork()
    if pid == 0:
        os._exit(i)  # Cada hijo sale con código diferente
    hijos.append(pid)

# Esperar a cada hijo específicamente
for pid in hijos:
    _, status = os.waitpid(pid, 0)  # 0 = bloquear
    print(f"Hijo {pid} terminó con código {os.WEXITSTATUS(status)}")

# Con WNOHANG: no bloquear si el hijo no terminó
pid, status = os.waitpid(-1, os.WNOHANG)
if pid == 0:
    print("Ningún hijo terminó todavía")