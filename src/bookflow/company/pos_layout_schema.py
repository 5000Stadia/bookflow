"""Company POS layout: boxes and an independent revision for ordered, atomic edits."""
import sqlalchemy as sa


def company_columns(C):
    return (
        C('pos_home_view_style', sa.String(12), 'Saved POS home style: block, list, compact or photo.',
          nullable=False, server_default='block'),
        C('pos_layout_version', sa.Integer, 'POS layout revision, independent of company settings. Zero means the '
          'category seed has not yet been saved; positive revisions also preserve an intentionally empty layout.',
          nullable=False, server_default='0'),
    )


def define_tables(metadata, C, T, common):
    boxes = T('pos_boxes', *common(),
        C('parent_id', sa.String(26), 'Parent group; null is the POS home.',
          sa.ForeignKey('pos_boxes.id', ondelete='RESTRICT'), nullable=True),
        C('position', sa.Integer, 'Zero-based position among the parent’s boxes.', nullable=False),
        C('kind', sa.String(8), 'group (opens boxes) or item (rings up the referenced item).', nullable=False),
        C('item_id', sa.String(26), 'Item being rung up; null for groups. Removing a box never removes its item.',
          sa.ForeignKey('items.id', ondelete='RESTRICT'), nullable=True),
        C('label', sa.String(400), 'Group name or optional item label; null uses the item name.', nullable=True),
        C('colour', sa.String(7), 'Optional tile colour, a six-digit hexadecimal CSS colour.', nullable=True),
        C('picture_attachment_id', sa.String(26), 'Group picture; item boxes always use their inventory item’s picture.',
          sa.ForeignKey('attachments.id', ondelete='RESTRICT'), nullable=True),
        C('view_style', sa.String(12), 'Saved style of this group’s contents: block, list, compact or photo.',
          nullable=False, server_default='block'),
        sa.CheckConstraint("kind IN ('group','item')", name='ck_pos_boxes_kind'),
        sa.CheckConstraint("(kind='group' AND item_id IS NULL AND label IS NOT NULL) OR "
                           "(kind='item' AND item_id IS NOT NULL AND picture_attachment_id IS NULL)",
                           name='ck_pos_boxes_contents'),
        sa.CheckConstraint("view_style IN ('block','list','compact','photo')", name='ck_pos_boxes_style'),
        sa.CheckConstraint('position >= 0', name='ck_pos_boxes_position'),
        sa.Index('ix_pos_boxes_parent_position', 'parent_id', 'position'),
        sa.Index('ix_pos_boxes_picture', 'picture_attachment_id'),
        description='Company-wide POS tap boxes, freely nested and ordered, independent of inventory categories. '
                    'Versioned working state; every edit also checks the independent layout revision.')
    return {'pos_boxes': boxes}
