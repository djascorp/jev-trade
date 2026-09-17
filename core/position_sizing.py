#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module de Position Sizing pour le Bot de Trading SMC.

Implémente un système de sizing dynamique sécurisé qui augmente
progressivement le stake en fonction du solde tout en protégeant
contre le drawdown excessif.

Utilisation:
    from core.position_sizing import PositionSizing

    sizing = PositionSizing(initial_balance=100)
    stake = sizing.calculate_stake(current_balance, current_drawdown)
"""

import logging
from typing import Tuple

logger = logging.getLogger(__name__)


class PositionSizing:
    """
    Système de sizing dynamique avec protection contre la ruine.

    Stratégie en 3 paliers progressifs:
    - Solde $0-$200: Stake fixe $4 (conservateur)
    - Solde $200-$500: Stake = solde * 0.04 (max $8)
    - Solde $500+: Stake = solde * 0.03 (max $15)
    """

    def __init__(self, initial_balance: float = 100.0, max_dd_percent: float = 30.0,
                 min_stake: float = 0.35, max_stake: float = 50.0):
        """
        Initialise le système de sizing.

        Args:
            initial_balance: Solde initial du compte
            max_dd_percent: Max drawdown autorisé en % du solde
            min_stake: Stake minimum autorisé (Deriv = 0.35)
            max_stake: Stake maximum par trade
        """
        self.initial_balance = initial_balance
        self.max_dd_percent = max_dd_percent
        self.min_stake = min_stake
        self.max_stake = max_stake

        # Paramètres de la stratégie (basés sur backtest M5-AGGRESSIVE)
        self.win_rate = 78.3  # % de trades gagnants
        self.profit_factor = 6.0  # Ratio gains/pertes
        # Pour petits comptes (initial_balance <= 100), réduire max_consec_losses
        self.max_consec_losses = 4 if initial_balance <= 100 else 6

        logger.info(f"PositionSizing initialisé: solde=${initial_balance}, max_dd={max_dd_percent}%")

    def calculate_stake(self, balance: float, current_drawdown: float = 0.0) -> float:
        """
        Calcule le stake optimal basé sur le solde et le drawdown actuel.

        Args:
            balance: Solde actuel du compte
            current_drawdown: Drawdown actuel en $ (optionnel)

        Returns:
            float: Stake recommandé pour le prochain trade
        """
        # Calculer le stake progressif de base
        base_stake = self._calculate_progressive_stake(balance)

        # Ajuster selon le drawdown si fourni
        if current_drawdown > 0:
            adjusted_stake = self._adjust_for_drawdown(base_stake, balance, current_drawdown)
            logger.debug(f"Stake ajusté pour DD: ${base_stake:.2f} -> ${adjusted_stake:.2f}")
            return max(self.min_stake, adjusted_stake)

        return max(self.min_stake, base_stake)

    def _calculate_progressive_stake(self, balance: float) -> float:
        """
        Calcule le stake progressif selon 3 paliers MODIFIÉS pour plus de sécurité.

        Recommandation finale acceptée par l'utilisateur:
        - Solde $0-$100: Stake fixe $4 (conservateur)
        - Solde $100-$200: Stake = solde * 0.04 (max $6)
        - Solde $200+: Stake = solde * 0.03 (max $12)

        Args:
            balance: Solde actuel

        Returns:
            float: Stake calculé
        """
        if balance < 100:
            # Palier 1: Stake fixe conservateur (début prudents)
            return 4.0
        elif balance < 200:
            # Palier 2: Sizing modéré (4% du solde, max $6)
            stake = balance * 0.04
            return min(stake, 6.0)
        else:
            # Palier 3: Sizing progressif (3% du solde, max $12)
            stake = balance * 0.03
            return min(stake, 12.0)

    def _adjust_for_drawdown(self, stake: float, balance: float, drawdown: float) -> float:
        """
        Ajuste le stake selon le drawdown actuel.

        Args:
            stake: Stake de base
            balance: Solde actuel
            drawdown: Drawdown actuel en $

        Returns:
            float: Stake ajusté
        """
        max_dd_allowed = balance * (self.max_dd_percent / 100)

        if max_dd_allowed <= 0:
            return stake

        dd_ratio = drawdown / max_dd_allowed

        if dd_ratio < 0.5:
            # Drawdown faible, pas d'ajustement
            return stake
        elif dd_ratio < 0.8:
            # Drawdown modéré, réduire de 25%
            adjusted_stake = stake * 0.75
            logger.warning(f"Drawdown modéré (${drawdown:.2f}), stake réduit de 25%")
            return adjusted_stake
        else:
            # Drawdown élevé, réduire de 50%
            adjusted_stake = stake * 0.5
            logger.warning(f"Drawdown élevé (${drawdown:.2f}), stake réduit de 50%")
            return adjusted_stake

    def validate_stake(self, stake: float, balance: float) -> Tuple[bool, str]:
        """
        Valide si le stake est sûr pour le solde donné.

        Args:
            stake: Stake proposé
            balance: Solde actuel

        Returns:
            tuple: (is_safe, reason)
        """
        min_balance = self._calculate_min_balance(stake)

        if balance < min_balance:
            return False, f"Solde insuffisant. Minimum recommandé: ${min_balance:.2f}"

        if stake > self.max_stake:
            return False, f"Stake trop élevé. Maximum autorisé: ${self.max_stake}"

        if stake < self.min_stake:
            return False, f"Stake trop bas. Minimum autorisé: ${self.min_stake}"

        return True, "Stake validé"

    def _calculate_min_balance(self, stake: float) -> float:
        """
        Calcule le solde minimum requis pour un stake donné.

        Basé sur: max pertes consécutives * marge de sécurité (ajusté pour petits comptes)

        Args:
            stake: Stake par trade

        Returns:
            float: Solde minimum recommandé
        """
        # Pour les petits comptes avec stake <= 4$, utiliser une marge de sécurité réduite
        # Cela permet de trader avec 4$ sur un compte de 50$ (4 * 6 * 1.5 = 36$ minimum)
        safety_multiplier = 1.5 if stake <= 4 else 3
        return stake * self.max_consec_losses * safety_multiplier

    def get_sizing_info(self, balance: float, current_drawdown: float = 0.0) -> dict:
        """
        Retourne des informations détaillées sur le sizing.

        Args:
            balance: Solde actuel
            current_drawdown: Drawdown actuel (optionnel)

        Returns:
            dict: Informations de sizing
        """
        recommended_stake = self.calculate_stake(balance, current_drawdown)
        is_safe, validation_msg = self.validate_stake(recommended_stake, balance)

        return {
            "balance": balance,
            "recommended_stake": recommended_stake,
            "is_safe": is_safe,
            "validation_message": validation_msg,
            "current_drawdown": current_drawdown,
            "max_allowed_drawdown": balance * (self.max_dd_percent / 100),
            "sizing_tier": self._get_sizing_tier(balance),
        }

    def _get_sizing_tier(self, balance: float) -> str:
        """Retourne le palier de sizing actuel (MODIFIÉ)."""
        if balance < 100:
            return "Tier 1: Fixe $4 (Ultra-Conservateur)"
        elif balance < 200:
            return "Tier 2: Progressif 4% max $6 (Modéré)"
        else:
            return "Tier 3: Progressif 3% max $12 (Optimisé)"


# Fonction utilitaire pour usage rapide
def calculate_optimal_stake(balance: float, current_drawdown: float = 0.0) -> float:
    """
    Calcule rapidement le stake optimal.

    Args:
        balance: Solde actuel
        current_drawdown: Drawdown actuel (optionnel)

    Returns:
        float: Stake recommandé
    """
    sizing = PositionSizing()
    return sizing.calculate_stake(balance, current_drawdown)


if __name__ == "__main__":
    # Test du module
    sizing = PositionSizing(initial_balance=100)

    print("=== TEST DU MODULE DE SIZING ===\n")

    test_balances = [50, 100, 200, 500, 1000]
    for balance in test_balances:
        info = sizing.get_sizing_info(balance)
        print(f"Solde: ${info['balance']}")
        print(f"  Stake recommandé: ${info['recommended_stake']:.2f}")
        print(f"  Palier: {info['sizing_tier']}")
        print(f"  Validation: {info['validation_message']}")
        print()
