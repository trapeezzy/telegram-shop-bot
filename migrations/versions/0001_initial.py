"""initial

Revision ID: 0001
Revises: 
Create Date: 2026-10-29 23:08:40.305656

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('categories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=64), nullable=False),
    sa.Column('emoji', sa.String(length=8), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('full_name', sa.String(length=128), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('is_blocked', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'PAID', 'SHIPPED', 'DONE', 'CANCELLED', name='orderstatus'), nullable=False),
    sa.Column('delivery_method', sa.Enum('COURIER', 'PICKUP', name='deliverymethod'), nullable=False),
    sa.Column('customer_name', sa.String(length=100), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('address', sa.String(length=255), nullable=True),
    sa.Column('comment', sa.String(length=500), nullable=True),
    sa.Column('items_total', sa.Integer(), nullable=False),
    sa.Column('delivery_price', sa.Integer(), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('stock_issue', sa.Boolean(), nullable=False),
    sa.Column('telegram_charge_id', sa.String(length=255), nullable=True),
    sa.Column('provider_charge_id', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('paid_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orders_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_orders_user_id'), ['user_id'], unique=False)

    op.create_table('products',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('category_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('stock', sa.Integer(), nullable=False),
    sa.Column('emoji', sa.String(length=8), nullable=False),
    sa.Column('photo_file_id', sa.String(length=255), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('order_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('order_items')
    op.drop_table('products')
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_orders_user_id'))
        batch_op.drop_index(batch_op.f('ix_orders_status'))

    op.drop_table('orders')
    op.drop_table('users')
    op.drop_table('categories')
    # Типы enum в Postgres живут отдельно от таблиц
    sa.Enum(name='orderstatus').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='deliverymethod').drop(op.get_bind(), checkfirst=True)
