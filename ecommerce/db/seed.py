"""Seed the e-commerce database with demo data.

Run as:  python -m ecommerce.db.seed

Creates:
  * 8 top-level categories + 2-3 children each (≈ 20 leaf categories)
  * ~120 products spread across leaf categories
  * 1-3 SKUs per product (size/color variants)
  * 3-5 images per product (placeholder URLs)
  * 2 demo users + 1 address each
  * 3 coupons

Idempotent: re-running clears existing rows in dependency order first
(use --force to actually wipe; otherwise it skips if data exists).
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
from decimal import Decimal
from typing import Optional

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from ecommerce.config import settings
from ecommerce.db.base import Base, SessionLocal
from ecommerce.db.models import (
    Category, Coupon, Product, ProductImage, ProductSKU, User, UserAddress,
)

logger = logging.getLogger(__name__)

# Deterministic randomness so two seed runs produce identical data.
RNG = random.Random(42)


# --------------------------------------------------------------------------- #
# Category tree
# --------------------------------------------------------------------------- #
CATEGORY_TREE: list[tuple[str, list[tuple[str, str]]]] = [
    ("手机数码", [
        ("智能手机", "📱"),
        ("电脑办公", "💻"),
        ("摄影摄像", "📷"),
        ("智能穿戴", "⌚"),
    ]),
    ("家用电器", [
        ("电视", "📺"),
        ("空调", "❄"),
        ("冰箱", "🧊"),
        ("洗衣机", "🌀"),
    ]),
    ("服饰鞋包", [
        ("男装", "👔"),
        ("女装", "👗"),
        ("鞋靴", "👟"),
        ("箱包", "👜"),
    ]),
    ("美妆个护", [
        ("面部护肤", "🧴"),
        ("彩妆", "💄"),
        ("香水", "🌸"),
        ("个人清洁", "🪥"),
    ]),
    ("食品生鲜", [
        ("零食", "🍪"),
        ("饮料", "🥤"),
        ("生鲜", "🥩"),
        ("粮油", "🌾"),
    ]),
    ("家居家装", [
        ("家具", "🛋"),
        ("家纺", "🛏"),
        ("厨具", "🍳"),
        ("灯具", "💡"),
    ]),
    ("运动户外", [
        ("运动服饰", "👕"),
        ("健身器材", "🏋"),
        ("户外装备", "⛺"),
    ]),
    ("图书文娱", [
        ("图书", "📚"),
        ("文具", "✏"),
        ("乐器", "🎸"),
    ]),
]


# --------------------------------------------------------------------------- #
# Product templates per category (deterministic generator)
# --------------------------------------------------------------------------- #
BRANDS_BY_CAT = {
    "智能手机": ["XiaoMi", "HUAWEI", "Apple", "OPPO", "vivo", "Samsung"],
    "电脑办公": ["Lenovo", "DELL", "ASUS", "HP", "ThinkPad"],
    "摄影摄像": ["Canon", "Nikon", "Sony", "Fuji"],
    "智能穿戴": ["Apple", "HUAWEI", "XiaoMi", "Garmin"],
    "电视": ["XiaoMi", "Hisense", "TCL", "Skyworth"],
    "空调": ["Gree", "Midea", "Haier", "Aux"],
    "冰箱": ["Haier", "Midea", "Siemens", "Ronshen"],
    "洗衣机": ["Haier", "LittleSwan", "Midea", "Panasonic"],
    "男装": ["HLA", "PeaceBird", "Jack&Jones", "Uniqlo"],
    "女装": ["Only", "VERO MODA", "ZARA", "Chanel"],
    "鞋靴": ["Nike", "Adidas", "Anta", "LiNing"],
    "箱包": ["Samsonite", "VIP", "Coach", "LV"],
    "面部护肤": ["L'Oreal", "Estee Lauder", "SK-II", "Lancome"],
    "彩妆": ["MAC", "Dior", "YSL", "Chanel"],
    "香水": ["Chanel", "Dior", "Tom Ford", "Jo Malone"],
    "个人清洁": ["Safeguard", "Dove", "Nivea", "Colgate"],
    "零食": ["Three Squirrels", "Bestore", "Layz", "Pepsi"],
    "饮料": ["CocaCola", "NongfuSpring", "Wahaha", "Yili"],
    "生鲜": ["Freshippo", "JD Fresh", "Sun-Art"],
    "粮油": ["Arawana", "Fortune", "Julius"],
    "家具": ["IKEA", "Kuka", "Straight", "ZiIn"],
    "家纺": ["Luolai", "Fuanna", "Mendale", "Sleepman"],
    "厨具": ["SUPOR", "Midea", "ZWILLING", "CookerKing"],
    "灯具": ["OPPLE", "NVC", "FSL", "Yeelight"],
    "运动服饰": ["Nike", "Adidas", "Under Armour", "LiNing"],
    "健身器材": ["SHUA", "Decathlon", "Keep", "BH"],
    "户外装备": ["The North Face", "Columbia", "Marmot", "Arc'teryx"],
    "图书": ["CITIC", "MaiPai", "PressA", "PressB"],
    "文具": ["M&G", "Deli", "Staples", "Pilot"],
    "乐器": ["Yamaha", "Pearl River", "Gibson", "Casio"],
}


def _placeholder_image(seed: int, w: int = 600, h: int = 600) -> str:
    """Use picsum.photos for deterministic placeholder images."""
    return f"https://picsum.photos/seed/p{seed}/{w}/{h}"


def _gen_skus(product_id: int, base_price: Decimal, idx: int) -> list[ProductSKU]:
    """Generate 1-3 SKUs per product with size/color variants."""
    skus: list[ProductSKU] = []
    n = RNG.choice([1, 1, 2, 3])
    variants: list[tuple[str, Decimal]] = []
    if n == 1:
        variants = [("默认", base_price)]
    elif n == 2:
        variants = [("标准版", base_price), ("高配版", (base_price * Decimal("1.3")).quantize(Decimal("0.01")))]
    else:
        variants = [
            ("颜色:红色;尺寸:S", base_price),
            ("颜色:蓝色;尺寸:M", (base_price * Decimal("1.05")).quantize(Decimal("0.01"))),
            ("颜色:黑色;尺寸:L", (base_price * Decimal("1.1")).quantize(Decimal("0.01"))),
        ]
    for i, (spec, price) in enumerate(variants):
        skus.append(ProductSKU(
            product_id=product_id,
            sku_code=f"SKU-{product_id:05d}-{i+1}",
            spec=spec,
            price=price,
            stock=RNG.randint(0, 500),
            reserved=0,
            is_active=True,
        ))
    return skus


def _gen_product(leaf_cat_id: int, leaf_cat_name: str, idx: int) -> Product:
    brand = RNG.choice(BRANDS_BY_CAT.get(leaf_cat_name, ["Generic"]))
    price = Decimal(str(RNG.choice([
        9.9, 19.9, 29.9, 49.0, 69.0, 99.0, 129.0, 199.0,
        299.0, 499.0, 699.0, 999.0, 1499.0, 1999.0, 3999.0, 5999.0,
    ])))
    original = (price * Decimal(str(RNG.uniform(1.1, 1.5)))).quantize(Decimal("0.01"))
    title = f"{brand} {leaf_cat_name.rstrip('子')} {idx:03d} 专业款"
    subtitle = RNG.choice([
        "限时秒杀，库存有限", "新品上市，立享优惠", "热销爆款，先到先得",
        "正品保证，全国联保", "性价比之选，必买好物",
    ])
    return Product(
        category_id=leaf_cat_id,
        spu_code=f"SPU-{leaf_cat_id:03d}-{idx:04d}",
        title=title,
        subtitle=subtitle,
        description=f"这是{brand}品牌下的{leaf_cat_name}商品，编号 {idx:03d}。"
                    f"采用优质材料，做工精细，性能稳定，全国联保一年。"
                    f"支持7天无理由退换货，48小时内发货。",
        main_image=_placeholder_image(leaf_cat_id * 1000 + idx),
        price_min=price,
        price_max=price,
        original_price=original,
        brand=brand,
        tags=f"{leaf_cat_name},{brand},热销",
        sales_count=RNG.randint(10, 9999),
        rating_avg=Decimal(str(round(RNG.uniform(4.0, 5.0), 1))),
        rating_count=RNG.randint(5, 500),
        is_published=True,
    )


def _seed_categories(db: Session) -> dict[str, int]:
    """Insert the category tree, return map of leaf-name → id."""
    leaf_ids: dict[str, int] = {}
    sort_idx = 0
    for top_name, children in CATEGORY_TREE:
        top = Category(
            parent_id=None, name=top_name,
            slug=top_name, icon="🏷",
            sort_order=sort_idx, is_active=True,
        )
        db.add(top)
        db.flush()
        sort_idx += 1
        for ci, (child_name, icon) in enumerate(children):
            child = Category(
                parent_id=top.id, name=child_name,
                slug=child_name, icon=icon,
                sort_order=ci, is_active=True,
            )
            db.add(child)
            db.flush()
            leaf_ids[child_name] = child.id
    db.flush()
    return leaf_ids


def _seed_products(db: Session, leaf_ids: dict[str, int], count: int) -> int:
    """Insert `count` products spread across leaf categories."""
    leaves = list(leaf_ids.items())
    total_inserted = 0
    for i in range(count):
        leaf_name, leaf_id = leaves[i % len(leaves)]
        prod = _gen_product(leaf_id, leaf_name, i + 1)
        db.add(prod)
        db.flush()
        # Generate SKUs and update price_min/max.
        skus = _gen_skus(prod.id, prod.price_min, i + 1)
        for sku in skus:
            db.add(sku)
        if skus:
            prices = [s.price for s in skus]
            prod.price_min = min(prices)
            prod.price_max = max(prices)
        # Generate 3-5 images.
        n_images = RNG.randint(3, 5)
        for j in range(n_images):
            db.add(ProductImage(
                product_id=prod.id,
                url=_placeholder_image(prod.id * 100 + j, 800, 800),
                sort_order=j,
            ))
        total_inserted += 1
        if (i + 1) % 50 == 0:
            logger.info("seeded %d products...", i + 1)
    db.flush()
    return total_inserted


def _seed_users(db: Session) -> None:
    demo_users = [
        ("demo-user-1", "张三", "13800138001"),
        ("demo-user-2", "李四", "13800138002"),
        ("guest-demo", "访客", None),
    ]
    for uid, name, phone in demo_users:
        u = User(id=uid, nickname=name, phone=phone,
                 avatar=_placeholder_image(hash(uid) & 0xffff, 200, 200))
        db.add(u)
        db.flush()
        # Add one default address per user.
        db.add(UserAddress(
            user_id=uid, recipient=name,
            phone=phone or "13800000000",
            province="广东省", city="深圳市", district="南山区",
            detail="科技园南区T3栋8楼", is_default=True,
        ))
    db.flush()


def _seed_coupons(db: Session) -> None:
    coupons = [
        ("NEW10", "fixed", Decimal("10"), Decimal("50")),
        ("SAVE20", "fixed", Decimal("20"), Decimal("100")),
        ("PCT15", "percent", Decimal("15"), Decimal("200")),
    ]
    for code, dtype, value, min_amt in coupons:
        db.add(Coupon(
            code=code, discount_type=dtype, discount_value=value,
            min_order_amount=min_amt, is_active=True,
        ))
    db.flush()


def _wipe(db: Session) -> None:
    """Delete all seeded data in dependency-safe order."""
    for model in [
        Coupon, ProductImage, ProductSKU, Product, Category,
        UserAddress, User,
    ]:
        db.query(model).delete()
    db.flush()


def run_seed(count: int = 120, force: bool = False) -> None:
    db = SessionLocal()
    try:
        existing = db.query(Product).count()
        if existing > 0 and not force:
            logger.info("seed: %d products already exist, skipping (use --force to wipe)", existing)
            return
        if force:
            logger.info("seed: --force given, wiping existing data...")
            _wipe(db)

        logger.info("seed: inserting categories...")
        leaf_ids = _seed_categories(db)
        logger.info("seed: %d leaf categories", len(leaf_ids))

        logger.info("seed: inserting %d products...", count)
        n_prod = _seed_products(db, leaf_ids, count)

        logger.info("seed: inserting demo users + addresses...")
        _seed_users(db)

        logger.info("seed: inserting coupons...")
        _seed_coupons(db)

        db.commit()
        logger.info("seed: DONE — %d products, %d categories", n_prod, len(leaf_ids))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the e-commerce DB")
    parser.add_argument("--count", type=int, default=120, help="Number of products to seed")
    parser.add_argument("--force", action="store_true", help="Wipe existing data before seeding")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_seed(count=args.count, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
