import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

async function main() {
  console.log('Seeding default trading account...');
  await prisma.tradingAccount.upsert({
    where: { id: 'default-paper' },
    update: {},
    create: {
      id: 'default-paper',
      name: 'Paper Trading Account',
      accountType: 'PAPER',
      balance: 10000,
      isActive: true,
    },
  });
  console.log('Seed completed successfully.');
}

main()
  .catch((e) => {
    console.error('Error seeding account:', e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
