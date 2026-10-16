"""Item delegates: layer name with mini icon, type badges and status pills."""

from __future__ import annotations

from qgis.PyQt.QtCore import QRect, QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QFontMetrics, QIcon, QPalette
from qgis.PyQt.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..core.model import Status
from ..core.texts import status_label
from .styles import badge_color, draw_pill, small_font, status_color, status_icon
from .tree_model import (
    BADGES_ROLE,
    IS_GROUP_ROLE,
    MINI_ICON_ROLE,
    SHORT_REASON_ROLE,
    STATUS_ROLE,
    SUMMARY_ROLE,
    VISIBLE_ROLE,
)

H_PAD = 7      # horizontal padding inside a pill
V_PAD = 2      # vertical padding inside a pill
GAP = 4


def _style(option):
    return option.widget.style() if option.widget is not None else QApplication.style()


def _two_line_height(option) -> int:
    fm = QFontMetrics(option.font)
    sfm = QFontMetrics(small_font(option.font))
    return fm.height() + sfm.height() + 4 * V_PAD + 4


def _draw_background(delegate, painter, option, index):
    """Selection / hover / BackgroundRole without text or icon."""
    opt = QStyleOptionViewItem(option)
    delegate.initStyleOption(opt, index)
    opt.text = ""
    opt.icon = QIcon()
    _style(option).drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, option.widget)


def _text_role(option) -> QPalette.ColorRole:
    if option.state & QStyle.StateFlag.State_Selected:
        return QPalette.ColorRole.HighlightedText
    return QPalette.ColorRole.Text


class NameDelegate(QStyledItemDelegate):
    """Default painting + dimmed name for hidden layers + mini provider icon after the name."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.data(VISIBLE_ROLE) is False:
            color = option.palette.color(QPalette.ColorRole.Text)
            color.setAlpha(130)
            option.palette.setColor(QPalette.ColorRole.Text, color)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width() + 20, max(size.height(), _two_line_height(option)))

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        mini = index.data(MINI_ICON_ROLE)
        if mini is None:
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text_rect = _style(option).subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, option.widget)
        fm = QFontMetrics(opt.font)
        text_w = fm.horizontalAdvance(opt.text)
        size = max(10, int(fm.height() * 0.8))
        x = text_rect.left() + min(text_w, text_rect.width() - size - 4) + 6
        if x + size > option.rect.right():
            return
        y = option.rect.center().y() - size // 2
        mini.paint(painter, QRect(x, y, size, size))


class BadgeDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        badges = index.data(BADGES_ROLE) or []
        f_big = small_font(option.font, 0.88)
        f_small = small_font(option.font, 0.78)
        width = GAP
        for i, b in enumerate(badges[:3]):
            fm = QFontMetrics(f_big if i == 0 else f_small)
            width += fm.horizontalAdvance(b.text) + 2 * H_PAD + GAP
        return QSize(min(width, 260), _two_line_height(option))

    def paint(self, painter, option, index):
        _draw_background(self, painter, option, index)
        badges = index.data(BADGES_ROLE) or []
        if not badges:
            return
        rect = option.rect.adjusted(GAP, 0, -GAP, 0)
        f_big = small_font(option.font, 0.88)
        f_small = small_font(option.font, 0.78)
        fm_big = QFontMetrics(f_big)
        fm_small = QFontMetrics(f_small)
        x = float(rect.left())
        cy = rect.center().y()
        right = rect.right()
        for i, badge in enumerate(badges):
            primary = i == 0
            font = f_big if primary else f_small
            fm = fm_big if primary else fm_small
            w = fm.horizontalAdvance(badge.text) + 2 * H_PAD + 2
            h = fm.height() + 2 * V_PAD
            remaining = len(badges) - i
            if x + w > right:
                if primary:
                    w = max(24.0, right - x)
                    text = fm.elidedText(badge.text, Qt.TextElideMode.ElideRight, int(w - 2 * H_PAD))
                    draw_pill(painter, QRectF(x, cy - h / 2.0, w, h), text, badge_color(badge.color), font, True)
                else:
                    more = f"+{remaining}"
                    mw = fm_small.horizontalAdvance(more) + 2 * H_PAD
                    if x + mw <= right:
                        draw_pill(painter, QRectF(x, cy - h / 2.0, mw, h), more, QColor("#7a7f87"), f_small)
                break
            draw_pill(painter, QRectF(x, cy - h / 2.0, w, h), badge.text, badge_color(badge.color), font,
                      strong=primary, outline=badge.color == "red")
            x += w + GAP


class StatusDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(180, _two_line_height(option))

    def paint(self, painter, option, index):
        _draw_background(self, painter, option, index)
        rect = option.rect.adjusted(GAP, V_PAD, -GAP, -V_PAD)
        if index.data(IS_GROUP_ROLE):
            summary = index.data(SUMMARY_ROLE) or ""
            painter.save()
            f = small_font(option.font, 0.92)
            painter.setFont(f)
            col = option.palette.color(_text_role(option))
            col.setAlpha(170)
            painter.setPen(col)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                             QFontMetrics(f).elidedText(summary, Qt.TextElideMode.ElideRight, rect.width()))
            painter.restore()
            return

        value = index.data(STATUS_ROLE)
        if not value:
            return
        status = Status(value)
        reason = index.data(SHORT_REASON_ROLE) or ""
        f_pill = small_font(option.font, 0.88)
        f_reason = small_font(option.font, 0.82)
        fm_pill = QFontMetrics(f_pill)
        fm_reason = QFontMetrics(f_reason)
        pill_h = fm_pill.height() + 2 * V_PAD
        total_h = pill_h + (fm_reason.height() + 2 if reason else 0)
        top = rect.top() + max(0, (rect.height() - total_h) // 2)

        icon_size = pill_h - 2
        status_icon(status, icon_size).paint(painter, QRect(rect.left(), top + 1, icon_size, icon_size))
        label = status_label(status)
        pill_w = fm_pill.horizontalAdvance(label) + 2 * H_PAD + 4
        pill_x = rect.left() + icon_size + GAP
        pill_w = min(pill_w, max(30, rect.right() - pill_x))
        label = fm_pill.elidedText(label, Qt.TextElideMode.ElideRight, int(pill_w - 2 * H_PAD + 2))
        draw_pill(painter, QRectF(pill_x, top, pill_w, pill_h), label, status_color(status), f_pill, strong=True)

        if reason:
            painter.save()
            painter.setFont(f_reason)
            col = option.palette.color(_text_role(option))
            if not option.state & QStyle.StateFlag.State_Selected:
                col.setAlpha(165)
            painter.setPen(col)
            reason_rect = QRect(rect.left(), top + pill_h + 2, rect.width(), fm_reason.height())
            painter.drawText(reason_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             fm_reason.elidedText(reason, Qt.TextElideMode.ElideRight, rect.width()))
            painter.restore()
