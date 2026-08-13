import json

with open('backend/validation-output/validation-1786440858515.json') as f:
    data = json.load(f)

for r in data['results']:
    if r['symbol'] == 'XRPUSD.P':
        print(f'=== {r["symbol"]} ===')
        for ob in r['orderBlocks']:
            print(f'  {ob["direction"]} | upper={ob["upperPrice"]:.6f} lower={ob["lowerPrice"]:.6f} width%={ob["widthPercent"]:.6f}%')