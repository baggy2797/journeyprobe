const cart = () => JSON.parse(localStorage.getItem("cart") || "[]");
const saveCart = (c) => localStorage.setItem("cart", JSON.stringify(c));
const params = new URLSearchParams(location.search);
const money = (n) => "$" + n.toFixed(2);
function paintCount() {
  const el = document.querySelector('[data-testid="cart-count"]');
  if (el) el.textContent = String(cart().length);
}
paintCount();
const q = params.get("q") || "";
const box = document.querySelector('[data-testid="search-input"]');
if (box) box.value = q;

const results = document.querySelector('[data-testid="results"]');
if (results) {
  const hits = PRODUCTS.filter((p) => p.name.toLowerCase().includes(q.toLowerCase()));
  results.innerHTML = hits.length
    ? hits.map((p) => `<li data-testid="result"><a href="product.html?id=${p.id}">${p.name}</a> <span>${money(p.price)}</span></li>`).join("")
    : '<li data-testid="no-results">No products found</li>';
}

const detail = document.querySelector('[data-testid="product"]');
if (detail) {
  const p = PRODUCTS.find((x) => x.id === Number(params.get("id")));
  if (p) {
    detail.querySelector("h1").textContent = p.name;
    detail.querySelector('[data-testid="price"]').textContent = money(p.price);
    detail.querySelector("p").textContent = p.blurb;
    detail.querySelector('[data-testid="add-to-cart"]').addEventListener("click", () => {
      saveCart([...cart(), p.id]);
      paintCount();
      document.querySelector('[data-testid="toast"]').textContent = "Added to cart";
    });
  }
}

const items = document.querySelector('[data-testid="cart-items"]');
if (items) {
  const lines = cart().map((id) => PRODUCTS.find((p) => p.id === id));
  items.innerHTML = lines.length
    ? lines.map((p) => `<li data-testid="cart-item">${p.name} - ${money(p.price)}</li>`).join("")
    : '<li data-testid="cart-empty">Your cart is empty</li>';
  document.querySelector('[data-testid="cart-total"]').textContent = money(lines.reduce((a, p) => a + p.price, 0));
}
