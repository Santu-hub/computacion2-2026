import sys
suma=0
for i in sys.argv[1:]:
    try:
        numero=float(i)
        suma+=numero
    except ValueError:
        continue 

print(f"Suma: {suma}")
