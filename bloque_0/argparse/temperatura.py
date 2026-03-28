import argparse

parser= argparse.ArgumentParser(description="Convertidor Temperatura")
parser.add_argument("valor",type=float,help="Temperatura a Convertir")
parser.add_argument("-t","--to",choices=["celcius","fahrenheit"],required=True,help="Unidad a convertir")
args = parser.parse_args()

if args.to=="fahrenheit":
    resultado=(args.valor*9/5)+32
    print(f"{args.valor}C = {resultado}F")

else:
    resultado=(args.valor-32)*9/5
    print(f"{args.valor}F = {resultado}C")