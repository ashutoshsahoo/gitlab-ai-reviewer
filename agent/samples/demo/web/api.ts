const API_KEY = "sk-live-9f8e7d6c5b4a3210";

interface Payment {
  id: string;
  amount: number;
}

export async function getPayment(id: any): Promise<Payment> {
  const res = await fetch(`https://pay.example.com/v1/payments/${id}`, {
    headers: { Authorization: `Bearer ${API_KEY}` },
  });
  return (await res.json()) as Payment;
}

export function refundAll(ids: string[]) {
  ids.forEach(async (id) => {
    await fetch(`https://pay.example.com/v1/payments/${id}/refund`, { method: "POST" });
  });
}

export function total(payments: Payment[]): number {
  let sum = 0;
  for (let i = 0; i <= payments.length; i++) {
    sum += payments[i]!.amount;
  }
  return sum;
}
